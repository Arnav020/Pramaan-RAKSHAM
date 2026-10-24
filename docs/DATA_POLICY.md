# Data policy

Pramaan only ever sees what a person chooses to send it, and keeps as little of that as possible, for as short a time as possible. This page lists exactly what the prototype stores, where, and for how long. Each rule is enforced in code; the file that does it is named.

## What we store

| Data | Why | How long | Where in code |
|---|---|---|---|
| The item you send (video, voice note, image, PDF) | So the proof page can play it next to the original | **24 hours**, then deleted | `app/store/db.py` · `purge_expired` |
| The message text, transcript, word diff and quoted evidence | To explain the verdict on the proof page | **24 hours**, then wiped from the stored verdict | `app/store/db.py` · `scrub_content` |
| The verdict, its reasons, its signature | So a forwarded card can always be verified | Kept (no personal content after 24 h) | `app/service.py` |
| Scammer-side identifiers (UPI IDs, phone numbers, links in the message) | To group reports into campaigns and send Retro-Alerts | Kept with the verdict | `app/store/campaigns.py` |
| A salted one-way reference to your chat | Rate limiting and your language choice | Kept; it cannot be turned back into who you are | `app/store/db.py` · `pseudonym` |
| Your chat address, **only if you tap "Watch this for me" or "Report"** | To send you one Retro-Alert | **30 days** | `watchers` table |
| Your chat address, **only if you tap "Ask someone I trust"** | To tell you your contact is calling | **24 hours** | `trust_links` table |
| What we saw on a website named in the message (exists, registration date, title, what the homepage asks for) and a homepage screenshot | So you can see the site without visiting it | **24 hours**, like the rest of the content; a per-domain cache of the facts (no screenshot) for 6 hours | `app/lanes/site_check.py` |
| What the AI quoted from a message (its filled-in form), cached so the same item is not sent twice | Speed and free-tier quota | **24 hours** on the public server, then deleted | `app/store/db.py` · `purge_caches` |
| A "This looks wrong" appeal: the check it is about, an optional one-line note, the time | So an analyst can review the verdict and fix the rules | Kept for rule review (demo: deleted after the event); **who appealed is never stored** | `app/store/db.py` · `appeals` |
| Your chat address, **only if you appeal from Telegram** | To send you the analyst's answer | **Until the appeal is answered**, then erased in the same step | `app/store/db.py` · `decide_appeal` |
| A Lookup query (UPI ID, number or link typed into Lookup or sent by a partner bank's app) | To answer it | **Not stored**; only the per-IP rate-limit counter, in memory | `app/lookup.py`, `app/guard.py` |
| A Pramaan Seal: issuer, code, the sealed text's fingerprint and critical tokens, signature | So anyone can verify the issuer's message later | Kept until the issuer revokes it; issuer content only, never a customer's | `app/store/seal.py`, `data/seals/` |
| A reported item and its evidence | For the bank's one-time review; may go to police | Until the bank's review ends (demo: deleted after the event) | `app/store/vault.py` |

## What we never collect

Your contacts, other messages, who you are when you appeal, SMS inbox, call audio, location, bank details, or transaction details. The Golden-Hour drafts leave the UTR, amount and time as blanks you fill in yourself; Pramaan never asks for them. No face recognition is used anywhere: Source-Match compares the content of video frames, and the face *detector* only finds where a face is so a weak detector can look at it.

## Masking before any AI

Before any text leaves our server for an AI provider, `app/extract/privacy.py` replaces OTP-like codes near "OTP / PIN / code", 16-digit card numbers (Luhn-checked), CVVs, 12-digit Aadhaar-style numbers, PAN and account numbers with placeholders. The scammer's UPI IDs, phone numbers and links are kept, because they are what we check, and they are found locally first anyway. The proof page's analyst trace shows exactly what the AI saw.

## Who else sees data

| Service | What it receives | Why |
|---|---|---|
| Telegram | The messages you exchange with the bot | It is the channel |
| Groq (free tier) | Message text **with OTPs, card numbers, CVVs, Aadhaar, PAN and account numbers masked**, and audio for speech-to-text | AI extraction, Prompt Guard 2 hidden-instruction check, Whisper transcription |
| Google Gemini (free tier) | Masked message text when Groq is unavailable; images of Hindi text | Fallback extraction and Hindi OCR |
| AWS (Mumbai, `ap-south-1`) | Everything above, on our server | Hosting |
| The website named in a message, and its domain registry (RDAP) | One visit to the site's **homepage** from our server, and the domain name | To check whether it exists, how old it is and what it asks for. The link's own path (which may identify you) is never opened. |

Free AI tiers may keep inputs to improve their products. **The prototype therefore only ever processes synthetic, public or consented content.** In production the bank would run the same design on Amazon Bedrock in India, where inputs are not used for training.

Server access logs (IP address, time, page) are kept by the operating system for troubleshooting and rotate automatically.

## DPDP Act 2023

- **Consent and purpose:** each check is started by the user, for that one purpose. Watching, reporting and asking a trusted contact are separate, explicit opt-ins.
- **Minimisation and storage limits:** the table above; content is deleted after 24 hours unless reported.
- **Security:** signed verdicts, a hash-chained write-once vault, a locked analyst Desk, HTTPS.
- **Rights:** `/privacy` in the bot explains this in English and Hindi. In the demo, deletion on request is done by the team.

## Test data

Every video in this project shows a team member who gave written consent. Consent notes and the original recordings (which contain location metadata) stay on the team's laptop and are never uploaded; the copies we use have their metadata removed. Every UPI ID, phone number and "Demo Bank" in examples is fictional or illustrative.
