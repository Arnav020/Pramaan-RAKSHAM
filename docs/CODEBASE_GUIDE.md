# Pramaan codebase guide (for Arnav and Krish)

Read this once, top to bottom (about 45 minutes). After that, use it to look things up. Every section ends with the questions a judge is likely to ask about that part, and how to answer.

> **Rule of thumb for the jury:** *AI proposes, rules dispose.* The AI models read, transcribe, fingerprint and detect. A small, versioned rule table makes the accountable decision. Every decision can be traced to facts with sources.

---

## 1. Pramaan in 60 seconds

**The one-line pitch:** Pramaan is the proof layer for money messages in India. It **proves** what's genuine (Pramaan Seal, trust rails), **exposes** what was changed (Source-Match, seal diffs, document match), and turns one analyst's confirmation into **protection** for everyone (Retro-Alerts, Lookup, pay-time pause).

A person forwards a suspicious message: text, a video, a voice note, a screenshot, a PDF notice or a UPI ID. Pramaan answers three independent questions in parallel:

| Lane | Question | Strong evidence it can find |
|---|---|---|
| **A · WHO** | Is the sender really who they claim to be? | A sealed notice whose text was changed, or a seal copied onto a scam; a UPI ID that breaks SEBI's `@valid` rule; a bank's look-alike website; a "bank" using an ordinary mobile number; a name on RBI's Alert List; a QR code that pays "Rahul Kumar", not RBI |
| **B · CHANGED** | Was this altered from a genuine original? | Same footage as a genuine official video but different words (**Source-Match**); a notice that borrows a real RBI circular number; an impossible circular number; hidden JavaScript in a PDF |
| **C · WHAT** | What does it ask you to do? | An OTP request; money demanded with pressure; a known scam playbook from SEBI, RBI or I4C advisories, also when reworded (semantic matcher) |

**The red rule:** "Matches a known scam pattern" needs **strong evidence from at least two different lanes**. Otherwise the answer is "Can't verify" (amber), which still tells the person not to act and how to check. Deepfake detectors only ever give *weak* hints, so a detector alone can never make anything red.

The person gets a **signed certificate card** (with a QR code anyone can scan to check the card is genuine), a **proof page** (side-by-side video with changed words highlighted), and next steps (1930, official links, a Golden-Hour Kit).

---

## 2. The journey of one check (follow the files)

```
Telegram / web / Android share
        │  app/channels/telegram.py   app/server.py  (/api/check, /share)
        ▼
app/service.py  check_and_record()         ← one entry point for every channel
        │
        ▼
app/pipeline.py  run_check()
   1. media prep (in parallel, per file)
        video → keyframes (app/media/prep.py) + audio → speech-to-text (app/media/stt.py, Groq Whisper)
        PDF / image → text, OCR, UPI QR codes, forensics (app/media/docs.py)
   2. extraction over message + transcript + document text
        app/extract/extractor.py  = rules first (contacts, claims, intent) + LLM that may only ADD facts
   3. three lanes in parallel
        Lane A  app/lanes/lane_a.py      (trust rails, data in app/rails/data/)
        Lane B  app/lanes/lane_b.py      (source_match.py, doc_match.py, detectors.py)
        Lane C  app/lanes/lane_c.py      (asks, pressure, playbooks.yaml)
   4. fusion  app/fusion/engine.py + rules_v2.yaml  → GREEN / AMBER / RED + rule id
   5. reasons app/explain/templates.py (fixed English + Hindi sentences)
        │
        ▼
app/service.py: serial number, Ed25519 signature (app/store/signing.py), saved in SQLite (app/store/db.py),
                proof evidence (thumbnails, word diff, document panel)
        │
        ▼
app/render/card.py → certificate PNG (headless Chromium)    app/server.py → /p/<id> proof, /v/<serial> verify, /lab
```

A text check takes about 1–5 s. A video takes about 1–4 s with cached models (first run 5–10 s). A Hindi screenshot needs Gemini vision the first time (about 10–30 s), then it's cached.

---

## 3. Folder map

```
pramaan/
  app/
    config.py           settings from .env (API keys, public URL, model routing)
    models.py           the data types (Extraction, Signal, LaneResult, Verdict)
    pipeline.py         orchestrates one check
    service.py          run + sign + store + build proof evidence
    server.py           FastAPI web server and routes
    guard.py            rate limits, Desk roles, issuer and partner keys
    routes_seal.py      Pramaan Seal: /s/CODE, issuer console
    routes_protect.py   Lookup page and API, pay-time mock, For-banks page
    lookup.py           what Pramaan knows about a UPI ID, number or link
    cli.py              developer tool: python -m app.cli "text" [--media file]
    extract/            contacts.py, claims.py, intent_rules.py, rules_extractor.py, llm.py, extractor.py
    rails/              registry.py + data/ (banks, regulators, UPI rules, Alert List, RBI circulars, playbooks, FRI mock)
    lanes/              lane_a.py, lane_b.py, lane_c.py, source_match.py, doc_match.py, detectors.py, reference_catalog.yaml
    fusion/             engine.py, rules_v2.yaml
    explain/            templates.py
    media/              prep.py (ffmpeg), stt.py (Whisper), docs.py (PDF, OCR, QR)
    store/              db.py (SQLite), signing.py (Ed25519)
    render/             card.py, guilloche.py, templates/ (card, proof, verify, lab, home, base)
    channels/           telegram.py
    static/             CSS design tokens, fonts, icon, manifest (Android share target)
  eval/                 dataset.yaml + public_set.yaml (labelled items), FROZEN.sha256 + PUBLIC_FROZEN.sha256,
                        run.py, redteam.py, robustness.py, semantic_calibrate.py, results/
  tools/                build_index, make_test_fakes, make_test_docs, fetch_rbi_circulars, load_alert_list
  tests/                pytest suite (158 tests)
  data/                 (git-ignored) media, index, cache, keys, database
```

---

## 4. Module by module

### 4.1 `app/models.py`: the shared vocabulary
- **`Signal`** is one finding: a `code` (e.g. `UPI_NOT_VALID_FOR_SEBI_CLAIM`), a `lane`, a `strength` (STRONG, WEAK, POSITIVE, INFO), `params` (the facts used in the sentence), and `provenance` (the dataset and URL our fact came from).
- **`LaneResult`** is a lane's status plus its signals.
- **`Verdict`** is the level, reasons, the rule that fired, the rules version and the lanes with strong evidence.

**Why:** every verdict can be explained and audited. Nothing is a bare score.

### 4.2 `app/extract/`: turning a messy message into facts
- **`contacts.py`** pulls out UPI IDs, phones, links, emails, SMS sender IDs, SEBI registration numbers, bank accounts and APK files with regular expressions.
  - It first undoes obfuscation: Unicode tricks, zero-width characters, Hindi digits, and `name @ ybl` with spaces.
  - `registrable_domain()` tells who owns a link (`sbi-bank.in.co` is owned by `in.co`, not SBI). It knows India's restricted zones (`.bank.in`, `.gov.in`).
- **`claims.py`** detects who the message claims to be: a bank, a regulator (RBI, SEBI, police, CBI, court), a SEBI intermediary, or a public figure. It searches only the prose, so `rbi.safe@okaxis` doesn't count as an Axis Bank claim.
- **`intent_rules.py`** holds the asks (pay, OTP, install, private group, remote access) and pressure tactics (urgency, secrecy, guaranteed returns, threats) in English, Hindi and Hinglish.
  - It also detects **hidden instructions** aimed at AI ("ignore previous instructions").
  - **`strip_negated()`** removes clauses like "we will never ask for your OTP" and warnings like "anyone who promises guaranteed returns is not telling the truth", so a bank's warning isn't mistaken for the scam. A scammer can't hide behind it: "SBI never asks for OTP. **But** share the OTP…" still counts.
- **`llm.py`** is one router over the free tiers: Groq `gpt-oss-120b`, then `gpt-oss-20b`, then `qwen3.8-27b`, then Gemini `3.1-flash-lite`. Answers are JSON only, cached on disk by content hash, and retried with backoff. It also provides `vision_transcribe()` (Gemini OCR fallback).
- **`extractor.py`** runs the rules first, then the LLM. Safety properties, each one tested:
  1. The message is passed as quoted data inside `<message>` tags, and the model is told never to follow instructions in it.
  2. The model fills a fixed JSON form. Unknown values are dropped.
  3. The model can only **add** facts. A contact point it proposes is kept only if the value literally appears in the message, so it can't invent a payment destination.
  4. Every ask, pressure tactic and claim it adds must come with a **verbatim quote** found outside warning clauses. Otherwise it's dropped.
  5. The model never sees or produces a verdict.

**Judge asks "what if the LLM is jailbroken by the message?"** It can only add grounded facts, never remove rule findings, never decide. The rule table decides. And the injection attempt itself becomes a visible warning sign.

### 4.3 `app/rails/`: India's trust rails, with provenance
Every data file starts with `_provenance`: dataset, source URL, fetch date and notes. The proof page cites it next to each finding.

| File | What | Honest status |
|---|---|---|
| `banks.json` | 24 banks with official domains (`.bank.in` and legacy), checked in DNS on 22 Oct; fictional Demo Bank on the reserved `.test` TLD | Real |
| `regulators.json` | RBI, SEBI, NPCI, I4C, DoT, police/CBI/courts; none collects money from the public | Real |
| `upi.json` | SEBI `@valid` rule (circular 11 Jun 2025) and category suffixes `.brk` / `.mf` | Real rule |
| `alert_list.json` | **All 95** entities on RBI's Alert List (updated 19 Nov 2025), parsed from the PDF, SHA-256 recorded | Real |
| `rbi_circulars.json` | 25 recent RBI circulars (RBI/2026-27/272–296), fetched politely | Real |
| `playbooks.yaml` | 10 scam playbooks, each citing a SEBI, RBI or I4C advisory | Real sources |
| `fri_mock.json` | DoT Fraud Risk Indicator | **MOCK**: only regulated entities get FRI |
| `sebi_register_snapshot.json` | SEBI register counts only | SEBI blocks bulk copying, so we check the reg-number format and send people to SEBI's search |

`registry.py` loads these once. Whole-word matching stops "Trive" from firing on "contrive" and "YES Bank" on "yesterday.com".

### 4.4 Lane A (`app/lanes/lane_a.py`)
For each contact point:
- **UPI:**
  - `@valid` with a category is POSITIVE.
  - A SEBI-adviser claim with a non-`@valid` UPI is STRONG.
  - RBI, police or a court asking money to a UPI ID is STRONG.
  - A bank asking money to a personal UPI ID is STRONG.
  - **QR payee:** a QR that pays "Rahul Kumar" for an "RBI" notice is STRONG.
- **Domain:**
  - an official bank or regulator domain is POSITIVE;
  - a look-alike (brand token plus words like kyc or login, or a close spelling) is STRONG;
  - punycode is STRONG;
  - a shortener or an unknown domain is WEAK.
- **Phone:**
  - a "bank" giving an ordinary mobile number is STRONG (TRAI's 1600-series mandate, Jan–Mar 2026);
  - a 1600-series number is INFO only (caller ID can be spoofed);
  - the FRI mock rated "Very High" is STRONG.
- **SMS header:** a known bank header with an -S or -T suffix is POSITIVE.
- **Alert List:** a match by name or by listed website is STRONG.

Status is CONTRADICTED if anything is strong, VERIFIED if every checkable contact point is positive, and UNVERIFIABLE otherwise.

**Judge asks "can't scammers just use an @valid handle?"** NPCI issues `@valid` handles only to SEBI-registered intermediaries. If a scam abuses a real one (a compromised account), Lane A says VERIFIED, but Lane C still sees the high-risk ask. "Verified plus suspicious" goes to amber and to the analyst queue (rule R4).

### 4.5 Lane B (`app/lanes/lane_b.py` and friends)
- **`source_match.py`: our signature idea.**
  1. Keyframes, 2 per second, are fingerprinted with **PDQ**, Meta's open perceptual hash and the industry standard for near-duplicate matching.
  2. Query frames are hashed in all 8 flips and rotations. The reference index stores centre crops (100%, 90%, 80%, 70%). So compression, mirroring, cropping and trimming still match.
  3. A reference counts as **the source** only if many frames match at **one consistent time offset**, found by scanning offsets over a full distance matrix. Krish standing still broke the naive "closest frame" version.
  4. Measured separation: the same clip after compression is about 8 bits apart (out of 256); a different genuine clip from the same wall and shirt is 100+ apart.
  5. Words are compared by **in-order coverage**: how much of what is said appears in the original, in order. It's robust to trimming and drops sharply when words are replaced. If the inserted words contain a payment, OTP or pitch, that's an automatic mismatch.
  6. If speech-to-text got the language wrong (low-quality Hindi), the audio is re-transcribed in the original's language.
  7. **No face recognition anywhere.** We never identify anyone.
- **`doc_match.py`:** RBI numbers circulars in sequence, so:
  - a real number used for a different subject gives "this is a genuine circular about X";
  - a number far beyond the latest one, or a future financial year, gives "cannot exist yet";
  - a faithful copy gives "reference checks out".
- **`detectors.py`: weak hints.**
  - **GenD** (CLIP ViT-L/14, the WACV 2026 method, trained on FaceForensics++) runs on face crops. OpenCV's Haar *detector* finds where a face is; it never identifies anyone.
  - A **wav2vec2** synthetic-speech classifier runs on the audio. It flagged every genuine voice on our data, so its reasons are **never shown on the card** (`user_visible` in `engine.py`). It stays a weak signal for analysts, and the Lab's "detectors fail" proof.
  - Scores go only to analysts and the Lab. Users never see a score.
- **Official original** (`reference_catalog.yaml`, `official:`): if the video *is* an unchanged copy of a clip the bank registered as its own, Lane B says `OFFICIAL_ORIGINAL` (positive), which can make it green (rule R2b).
- **PDF forensics** (`app/media/docs.py`): consumer editing software in the metadata (Canva, Word, iLovePDF); later edits (incremental saves); **hidden JavaScript, launch actions, embedded files**; links.

**Judge asks "what if the deepfake is generated from scratch, with no original?"** Source-Match won't fire, so Lane B only has weak hints. The verdict then rests on Lanes A and C, which still need two strong lanes for red. Without them it's amber. This is stated as a limit.

### 4.6 Lane C (`app/lanes/lane_c.py`, `app/rails/data/playbooks.yaml`)
- **STRONG:** an OTP or PIN request, remote access, a sideloaded APK, money demanded with guaranteed returns, threats or too-good offers, or a full match of a known playbook with a high-risk ask.
- **WEAK:** single pressure tactics, a private-group invite, hidden instructions.
- Playbooks (19) are keyword groups with required groups and a coverage score. Each cites its advisory, shown on the proof page.
- **`semantic.py`**: used when no keyword playbook matches.
  - Sentences are compared with `multilingual-e5-small` against our scam and genuine exemplar sentences (`playbook_exemplars.yaml`).
  - The score is a *margin*: similarity to scam minus similarity to genuine. Thresholds are 0.035 and 0.065, calibrated on dev only (`eval/semantic_calibrate.py`).
  - A semantic-only match also needs an ask or pressure, or a strong margin, so a genuine interview isn't tagged.
  - Honest result: it doesn't change how many scams are flagged. It improves naming the right pattern (80 → 89 of 100 rewrites, re-run 24 Oct).
- `hf_lock.py`: one lock around Hugging Face model loading, because two threads importing `transformers` at once broke it.

### 4.7 Fusion (`app/fusion/engine.py`, `rules_v2.yaml`)
Rules are evaluated top to bottom, and the first match wins:
1. **R1** RED if strong evidence comes from 2 or more different lanes.
2. **R2** GREEN if the text carries a valid, unrevoked Pramaan Seal and nothing strong was found (`SEALED`).
3. **R2b** GREEN if the video is an unchanged official original and Lane C isn't HIGH.
4. **R3** GREEN if Lane A is VERIFIED, Lane B found nothing, and Lane C isn't HIGH.
5. **R4** AMBER plus analyst if Lane A is VERIFIED but something is suspicious. This is a possible account compromise, with its own Desk section.
6. **R5** AMBER otherwise.

Amber also gets a **kind** (`_amber_kind`), each with its own advice (`ADVICE`):
- `high_risk`: "don't act";
- `nothing_to_check`: "how to check yourself";
- `unverified`: "use the official number".

Up to three reasons are picked, one per lane first, with visible proof (Source-Match, a document mismatch, the QR payee) first.

### 4.8 Explainer (`app/explain/templates.py`)
There's a fixed English and Hindi sentence for every reason code. Wording rules: never "fake", never "fraudster"; say what failed to match. Titles are "Verified sender", "Can't verify", "Matches a known scam pattern".

### 4.9 Media (`app/media/`)
- `prep.py`: ffmpeg probing, keyframes and audio, all bounded (3 minutes, 90 frames). `strip_metadata()` removes GPS (your iPhone clips carried the recording location).
- `stt.py`: Groq `whisper-large-v3` with word timestamps, cached by audio hash.
- `docs.py`:
  - PDF text (pypdf) and page rendering (PDFium);
  - OCR (RapidOCR locally; a noise detector sends Hindi or hard images to Gemini vision);
  - UPI QR decoding (OpenCV: real UPI ID, payee name, amount);
  - forensics.

### 4.10 Service, store and signing (`app/service.py`, `app/store/`)
- Each check gets a banknote-style serial (`PR-261022-0017`) and an **Ed25519 signature** over `{serial, level, issued time, reason codes, digest of reason text, key id}`.
  - Prototype: the key lives in `data/keys/` and is never committed.
  - Production: AWS KMS.
- SQLite tables:
  - `checks`;
  - `prefs` (language);
  - `watchers` ("Watch this for me", explicit opt-in, deleted after 30 days);
  - `counters` (serials).
- Chats are stored only as **salted pseudonyms**. Media is copied only for the proof page and **deleted after 24 hours** (`purge_expired`).

**Judge asks "can a scammer fake a green Pramaan card?"** They can edit a screenshot, but they can't produce a valid signature. Scanning the QR shows "Genuine Pramaan verdict" with what Pramaan actually said, or "Pramaan never issued this card".

### 4.11 Rendering (`app/render/`)
- `card.py`: HTML to PNG through headless Chromium (Playwright), kept warm (about 0.4 s per card).
  - The card shows: guilloché background (`guilloche.py`, drawn in code), rubber stamp, serial, signed QR, the original-vs-forwarded frames, the changed-words strip, the document panel and three reasons.
  - It's bilingual: Hindi first if the user prefers.
- Templates: `proof.html` (synced side-by-side playback, word-level highlights, frame-match strip, rails with sources, playbook, actions, decision trace), `verify.html`, `lab.html`, `home.html`.
- Fonts are bundled (OFL) so cards look the same on Linux servers.

### 4.12 Telegram bot (`app/channels/telegram.py`)
- Long polling, so no public webhook is needed.
- It replies "Checking…" right away, then sends the card with four buttons:
  - 👪 Family: warn them, or "Ask someone I trust";
  - 💸 I already paid: the Golden-Hour Kit (1930 call script, NCRP, bank email, full packet);
  - 🚩 Report to the bank;
  - ⋯ More: watch, appeal, check officially, language.
  - On HTTPS a "See the proof" link button is added.
- 📞 "I'm on a call right now" switches the caption to "Hang up now" advice, without changing the verdict.
- A bare UPI ID or link also gets a Lookup answer.
- Rate limit: 12 checks per 10 minutes per chat (stops people testing scams against us).

### 4.13 Server (`app/server.py`)
| Route | What |
|---|---|
| `POST /api/check` | check API |
| `POST /share` | Android share target |
| `GET /card/<id>.png?lang=` | card image |
| `/p/<id>` | proof page |
| `/v/<serial>?s=` | verify page |
| `/lab` | trust board |
| `/media/...` | media for the proof page |
| `/.well-known/pramaan-verdict-key.pem` | public key |
| `/health` | health check |
| `/s/<code>` | seal page |
| `/issuer`, `/issuer/login` | issuer console |
| `/lookup`, `GET /api/v1/lookup?q=` | Lookup page and API |
| `/demo-bank/pay` | pay-time pause (mock) |
| `/partners` | For banks |
| `/desk`, `/desk/login` | analyst Desk |
| `/redteam` | red-team panel |

### 4.14 Report, Evidence Vault, campaigns, Desk, Retro-Alert (`app/store/vault.py`, `app/store/campaigns.py`, `/desk`)
- **Report** (bot button or proof-page button) is explicit opt-in. `service.report_check()` seals the item's files, card and signed verdict into the **vault**:
  - every file is stored under its SHA-256 and never overwritten;
  - every event goes on a **hash chain** (each record includes the previous record's hash);
  - each report gets an Ed25519-**signed manifest**.
  - `verify_chain()` catches any edit to history; there's a test that edits one record and proves it's caught. Production: S3 Object Lock and KMS.
- **Campaigns** group reports by shared *hard* indicators: `upi:`, `phone:`, `domain:`, `footage:<genuine clip>` (re-voiced), `circular:<RBI ref>` (borrowed or impossible). Official and verified contact points are never indicators. Every check, reported or not, is indexed, so the Desk can show reach ("people who saw this campaign").
- **Pramaan Desk** (`/desk`):
  - Sign in at `/desk/login`. The token goes into an HttpOnly cookie, never the URL.
  - Two roles:
    - **analyst** (`DESK_TOKEN`): confirm, reject, answer appeals;
    - **lead** (`DESK_LEAD_TOKEN`): also export packets and revoke Seals.
  - Tabs: campaigns, appeals, possible account compromise.
  - What it does:
  - one review per campaign, with Confirm or Reject (a note is required; it feeds the rule review);
  - a **signed packet** with mule-VPA referrals, numbers for Chakshu, domains for takedown, reused footage and borrowed circulars;
  - Pramaan itself contacts no one outside.
- **Confirm** causes two things:
  1. a **Retro-Alert** to every opted-in watcher of any matching check ("Update on something you checked… confirmed as a scam campaign… don't pay, call 1930"), sent once;
  2. **campaign memory**: from now on, any check containing those indicators gets a STRONG `CAMPAIGN_MEMORY_HIT` (contact points in Lane A, footage or circulars in Lane B).
- This is "review once, protect many", made real: one analyst decision protects past and future recipients.

**Judge asks "why not auto-confirm campaigns?"** High-impact actions need a human. Confirmation is the only path to alerts and packets, and every decision is on the hash chain.

### 4.16 Pramaan Seal (`app/store/seal.py`, `app/routes_seal.py`, `app/render/notice.py`)
- **What:** a bank signs its own genuine notice or SMS. The seal is a short code (`[A-HJ-NP-Z2-9]{8}`, no 0/O/1/I), printed on the message with a link to `/s/CODE`.
- **How:**
  - Each issuer has an Ed25519 key.
  - The signature covers a fingerprint of the *normalised* text plus its **critical tokens**: amounts, accounts, UPI IDs, links, phones, dates.
  - The registry is files in `data/seals/`.
- **Checking a message:** `codes_in()` finds a seal code, and `compare()` diffs the sealed text against what the user sent.
  - Identical → `SEALED` (positive) → green by R2.
  - Different → `SEAL_CONTENT_CHANGED` (strong), with the changed words listed. This also catches a genuine seal copied onto a scam.
  - Unknown code → `SEAL_NOT_ISSUED`. Bad signature → `SEAL_FORGED`. Withdrawn → `SEAL_REVOKED`.
- **Tolerant mode** for OCR and speech: small reading slips are ignored, but never a change to a critical token.
- **Issuer console** `/issuer` (sign in with an `ISSUER_TOKENS` token): seal a notice or SMS, download the sealed PDF or PNG, revoke (lead).
- Pramaan's own seal links are filtered out of extraction, so the seal itself is never counted as "an unofficial link".

**Judge asks "isn't this just C2PA?"** Same idea: the issuer signs. But a Seal works on what people actually forward: text, screenshots and photos of a notice. A C2PA manifest is stripped the moment WhatsApp re-compresses an image. C2PA for media stays on the roadmap.

### 4.17 Protect: Lookup, pay-time pause, For banks (`app/lookup.py`, `app/routes_protect.py`)
- `lookup(q)` answers for one UPI ID, number or link. Lookups are not stored. Possible answers:
  - `warning`: in a campaign a human confirmed;
  - `pattern`: rules alone see a known risk, such as a look-alike domain;
  - `official`: on our registry;
  - `no_warning` or `invalid`.
- `GET /api/v1/lookup?q=` takes an `X-Pramaan-Key` partner key. Partners have no public rate limit.
- `/demo-bank/pay` is a **mock** bank app. On a warning it pauses for 30 seconds and offers "Call someone I trust", but still lets you pay. Pramaan never blocks.
- `/partners` shows the integration options, each marked LIVE, BUILT or PLANNED.

### 4.18 Trust controls (`app/guard.py`, `app/extract/privacy.py`)
- **Web rate limit:** 20 checks per 10 minutes per IP (`PRAMAAN_WEB_RATE`). The real IP is read from Caddy's `X-Forwarded-For` only.
- **`mask_for_ai()`:** OTPs, cards (Luhn), CVVs, 12-digit IDs, account numbers and PAN are replaced before any AI call. The proof page's trace shows "What the AI saw".
- **Prompt Guard 2** (`injection_score` on the extraction): an extra hidden-instruction signal, threshold 0.5.
- **Appeals** ("This looks wrong"): stored without who appealed. The analyst upholds or dismisses each one.
- **`scrub_content()`:** wipes message text, transcripts and diffs from every check after 24 hours.

### 4.15 Evaluation (`eval/`)
- `dataset.yaml`: 66 labelled items, split into **dev** (the items I tuned on) and **test** (written fresh and **frozen** before the first measurement; the fingerprint is in `FROZEN.sha256` and the git timestamp proves the order).
- `run.py` computes:
  - the confusion matrix;
  - red precision, scam recall, false red on genuine, green on official, each with **Wilson 95% intervals**;
  - **ablation** (re-fusing the same lane outputs with each lane off);
  - the **key experiment** (detector alone vs Pramaan on videos, by forwarding condition);
  - latency p50/p95;
  - subgroups.
- Output goes to `eval/results/latest.json`, which `/lab` reads. **No number is typed by hand.**
- `baseline-frozen-first-run.json` is the untouched first measurement.
- **Public split** (`public_set.yaml`): 24 quoted real messages with sources, frozen in `PUBLIC_FROZEN.sha256` before any detection change. `baseline-public-before-phase3.json` is its first run.
- **`robustness.py`:**
  - 100 AI-made rewrites of frozen test scams, run with the semantic matcher on and off;
  - an unseen-family study: each item re-run with its own playbook removed.
- **`redteam.py`:** 29 attacks → `results/redteam.json` → `/redteam`.

---

### 4.19 Finale additions (24 Oct): where each one lives, in one line each

| What | File | The one-line "why" |
|---|---|---|
| Green can't be borrowed | `app/fusion/rules_v2.yaml` (v2.2, `no_signal: PLAYBOOK_MATCH`), `lane_a.py` `check_sms_header` (INFO) | A typed SMS header or a pasted official link is not proof of who sent a message. |
| "No warning signs" | `app/fusion/engine.py` `_amber_kind` → `no_risk_signs` | A message with nothing to check and nothing risky shouldn't look like a warning. |
| Seal edits by word order | `app/store/seal.py` `compare` (SequenceMatcher, negations critical) | Deleting "not" changes the meaning; a set of words can't see that. |
| Campaign poisoning guard | `app/store/campaigns.py` `_never_an_indicator` | Confirming a scam must not taint t.me, YouTube or a real bank's domain. |
| Write-once vault manifests | `app/store/vault.py` `seal_report` (`open(..., "x")`) | Evidence files are never overwritten, even by a second report. |
| DNS-rebinding guard | `app/lanes/site_check.py` `_connected_ip`, `app/render/card.py` `check_peer` | Check the address we actually connected to, not only the one DNS gave first. |
| Bare-IP links | `app/extract/contacts.py` `is_ip_host`, `lane_a.py` `DOMAIN_RAW_IP` | Real institutions never send you to `http://95.141.32.7/`. |
| Appeal answer loop | `app/store/db.py` `decide_appeal` / `upheld_appeal`, `app/server.py` `tell_appeal_outcome`, proof page button | The person who was wrongly flagged hears the answer; the verdict says "Corrected". |
| Listen (on-device voice) | `proof.html` (Web Speech API) | Low-literacy users hear the verdict; nothing leaves the phone. |
| Live Source-Match | `app/media/live_ref.py`, `app/routes_seal.py` `/issuer/video` | The jury can test Source-Match on their own footage. |
| Tiers and open dataset | `eval/run.py` `tier`, `eval/open_kit.py`, `eval/kit.py`, `eval/REPRODUCE.md` | Every false alarm counts, measured on data we never trained on, reproducible in one command. |
| Known misses | `eval/redteam.py` (`known_miss=True`) | Show what still beats us, and what we'd change. |

## 5. Running it

```bash
cd pramaan
.venv/Scripts/python -m pytest                       # all tests
.venv/Scripts/python -m app.cli "Pay ₹10,000 to profitwealth@ybl, guaranteed 300% returns, SEBI registered"
.venv/Scripts/python -m uvicorn app.server:app --port 8000   # web + Telegram bot
.venv/Scripts/python -m eval.run                     # refresh the Lab numbers
.venv/Scripts/python -m tools.build_index            # after adding genuine clips
.venv/Scripts/python -m tools.make_test_fakes        # consented test fakes
.venv/Scripts/python -m tools.make_test_docs         # forged / genuine test documents
```

---

## 6. Demo scenarios and the files behind them

| Scenario | Input | Expected | Built? |
|---|---|---|---|
The full stage script is `DEMO_SCRIPT.md`, outside the repo. The items are pre-checked on the server by `tools/prepare_demo.py`.

| Beat | Input | Expected |
|---|---|---|
| Prove | `sealed_notice_genuine.pdf` | 🟢 Verified sender (sealed by Demo Bank) |
| Prove | `sealed_notice_edited.pdf` | 🔴 genuine seal, text changed: amount and fee listed |
| Prove | `sealed_notice_copied.pdf` | 🔴 genuine seal on a different message |
| Expose | `fake_lipsync_clip1_arnav_en_wa.mp4` | 🔴 same footage, different words, original side by side |
| Expose | `genuine_clip3_krish_en_wa.mp4` | 🟢 Demo Bank's own video, unchanged |
| Expose | `forged_rbi_notice_realref.pdf` | 🔴 real circular about something else; QR pays a person |
| Expose | `genuine_clip1_arnav_en_lowq.mp4` | 🟡 never red, though detectors say "fake" |
| Protect | profitwealth@ybl on `/lookup` and `/demo-bank/pay`, before and after a Desk confirm | no warning, then a pause |

---

## 7. Honest limits (say these first)
1. Source-Match only covers footage we've indexed. A fully generated video falls back to Lanes A and C.
2. The SEBI `@valid` check is by pattern; SEBI Check has no public API. The register is not bulk-copied.
3. FRI is a MOCK. Real access needs a regulated partner bank.
4. Our test set is small and self-built. The Lab shows n and confidence intervals.
5. Registries lag. "Not on a list" is never treated as safe.
6. Free-tier LLMs may use inputs, so we send only synthetic, public or consented content.
7. Users still have to choose to forward. Mitigations: family warnings, Retro-Alerts, and Lookup at the bank's pay screen (mocked in the prototype).
8. Seals only help when the issuer seals. An unsealed genuine notice is amber.
9. The semantic matcher is modest: it names patterns better; it doesn't catch more.

**Not built:**
- S3 Object Lock and KMS. The vault is local, with the same interface.
- C2PA for media.
- The WhatsApp channel, which needs Meta business verification.

Also:
- We dropped the Hindi voice note on purpose.
- Android share is built but not yet tested on a device.
- The weak detectors are wired in; their measured failure is on the Lab.

---

## 8. Who defends what
- **Arnav:** Lane A rails, fusion rules, signing and verification, the store and privacy, the server and Telegram, the card and proof page, the evidence path and Desk.
- **Krish:** media prep, Source-Match (PDQ, offsets, word coverage), detectors and the key experiment, OCR and QR, the LLM extractor and its safety, playbooks, eval and the Lab.

Each of you should be able to explain the flow in section 2 end to end.

---

## 9. Twenty likely jury questions, with short answers

The fuller, current list (27 questions, including the overnight features) is `JURY_QA.md` in the project root, outside the repo.
1. **Isn't this just a deepfake detector?** No. Detectors are weak hints in our system. We check the claim against India's trust rails and find the genuine original. The Lab shows the detector alone vs Pramaan.
2. **Why not rely on detectors?** In-the-wild benchmarks show open detectors lose about half their accuracy (Deepfake-Eval-2024). WhatsApp compression makes genuine videos look fake. Our key experiment measures it on our clips: detectors alone called 15 of 15 genuine videos fake.
3. **Why two lanes for red?** One wrong data point (a stale registry, a detector error) must not accuse anyone. Two independent kinds of evidence must agree.
4. **What does amber mean for a real scam?** "Don't act yet, check through official channels", with the links. It still protects.
5. **How do you find the original video?** PDQ perceptual hashes of keyframes, matched at a consistent time offset. No face recognition.
6. **Couldn't a scammer edit the video to break the hash?** Light edits (compression, crop, mirror, trim) still match; that's tested. Heavy changes push it to "no source found", and then Lanes A and C decide.
7. **What if the LLM is prompt-injected?** It can only add grounded facts, it never decides, and the attack is shown as a warning sign.
8. **Where does your data come from?** Public regulator sources, cited with fetch dates on every finding. FRI is mocked.
9. **Did you scrape SEBI?** One page view. Their firewall blocked automation and we stopped. We don't bulk-copy the register.
10. **How do you handle Hindi?** Rules in Hindi and Hinglish, Whisper for speech, Gemini OCR for Devanagari text, reasons in Hindi.
11. **Privacy?** Only the shared item is kept, for 24 hours. Chats are salted pseudonyms. "Watch this for me" is opt-in for 30 days. GPS is stripped from media. No contacts, SMS, location or transactions are touched.
12. **Can people use Pramaan to test their scams?** There are no scores, only coarse verdicts, plus rate limits per chat and per IP. The decisive checks are rails, which a better fake can't beat.
13. **Can someone fake a Pramaan card?** No. Cards are signed (Ed25519), and the QR opens our verify page.
14. **Who runs this in reality?** The partner bank's fraud-risk team (FRM), under the CISO. Citizens use the bank's own verified WhatsApp number in production.
15. **Why Telegram, not WhatsApp?** WhatsApp's API needs Meta business verification, which a student team can't truthfully get. A bank already has it.
16. **How fast is it?** Text takes about 1–5 s and video about 1–4 s on our laptop (the Lab shows p50 and p95).
17. **Cost?** Free LLM tiers plus small AWS costs in the prototype. Production estimates are on the deck and labelled as estimates.
18. **What about the forged RBI notice?** RBI numbers circulars in sequence. We show the real circular with that number, or prove the number can't exist, and decode who the QR code really pays.
19. **What was built before the event?** Pre-event coding was allowed. `BUILD_LOG.md` and the git history show exactly what was built when.
20. **What are your limits?** Section 7. Say them before they ask.
