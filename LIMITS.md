# Limits, risks and assumptions

We would rather you hear these from us. Each limit says what it means for a user and what we do about it.

## What Pramaan cannot do

| Limit | What it means | What we do |
|---|---|---|
| **Source-Match only knows indexed footage.** The prototype index holds 3 consented clips of our team. | A deepfake made from footage we have never seen gets no "same footage, different words" proof. | Lanes A and C still decide on the payment rails and the ask. Roadmap: institutions and public figures opt in by registering their official channels. Their genuine videos are then indexed as content fingerprints, like YouTube's Content ID, never as faces. |
| **Fully AI-generated video has no original.** | Nothing to match against. | Same as above: the payment destination and the ask are checked, however good the face is. |
| **Heavy cropping weakens Source-Match.** Our cropped lip-sync fake still matches, but with a smaller margin than other variants. | A much tighter crop could break the match. | We tried indexing extra centre crops (70% and 55%). The match did not improve: the median distance to the same clip stayed 42, against ≥ 98 for a different clip. So we did not adopt it. Tested variants: WhatsApp compression, 80% crop, mirror, trim, blur. |
| **Weak detectors are weak.** On our own videos the open-source video detector flagged 6 of 15 genuine clips, and the voice model flagged all 15. | A detector score is never evidence on its own. | Capped at a weak hint; a detector can never make anything red. The voice model is not shown on the card at all. Both appear on the Lab only as the key experiment: detectors alone fail. |
| **The semantic matcher is modest.** On 100 rewritten scams it did not change how many were flagged. It raised correct pattern naming from 80 to 89 (re-run 24 Oct). | It helps name the pattern; it is not what catches scams. | A semantic match alone also needs an ask or pressure (or a very strong similarity) before it counts, so a genuine news interview is not tagged. |
| **No live call listening.** Android and iOS do not let apps hear phone or WhatsApp calls. | We cannot warn mid-call by listening. | "On a call" mode checks what the caller claims and asks for, and the calling number, from what the user types or records. |
| **Many scams stay amber.** 15 of 26 test scams and all 16 public scams were amber, not red. | Single-lane evidence (no payment ID or link to check) is not enough for red. | Deliberate: red needs two independent lanes, so we almost never accuse wrongly. Amber still says "don't act, check officially", and Retro-Alerts follow up once a campaign is confirmed. |
| **Seals only cover issuers who seal.** | An unsealed genuine notice is "Can't verify", not green. | Rails (domain, number, `@valid`) still apply. Seal checks on photos and transcripts use a tolerant mode: an OCR slip is ignored unless a critical token (amount, account, UPI ID, link, phone, date) differs. |
| **Lookup knows only what analysts confirmed.** | "No warning" on Lookup does not mean safe. | The page and the API say so on every "no warning" answer. |
| **SEBI `@valid` is checked by pattern.** SEBI Check has no public API, and we do not bulk-copy SEBI's register. | A fake ID with the right shape would pass the pattern. | In production the bank provides register data. The proof page links to SEBI's own search. |
| **DoT Fraud Risk Indicator is a mock.** | Three fictional numbers only. | Labelled MOCK everywhere. Production access goes through a regulated bank. |
| **RBI circular index is partial.** It holds 25 recent circulars (RBI/2026-27/272–296). | Older borrowed circular numbers are not checked. | "Not found" is never treated as safe. Production: a full nightly sync. |
| **Registries lag.** | "Not on the RBI Alert List" never means safe. | Stated in every reason that relies on a list. |
| **Hindi OCR uses Gemini vision.** The local OCR cannot read Devanagari. | Hindi screenshots depend on a free AI tier and its limits. | Results are cached. If the AI is unavailable, the check still runs on what it can read. |
| **Scam websites can hide from our visitor.** It announces itself honestly ("PramaanLinkCheck") and comes from one known server. | A site can show us a harmless page and show victims the scam (cloaking). | A clean homepage is never a reason for green; only Lane A's rails and Seals can verify. Production: visits from the bank's own varied infrastructure. |
| **Grooming messages carry no evidence yet.** The first "wrong number, let's be friends" message asks for nothing. | It comes back "No warning signs". | The ask, when it comes, is checked; "Watch this for me" and Retro-Alerts follow up. Shown as a known miss on `/redteam`. |
| **Languages beyond English, Hindi and Hinglish.** Phrase rules and playbooks cover those three. | A Tamil scam with a payment ID still came back red in our red team (LG-1), through the AI reader and the multilingual matcher. Without the AI reader (free tier down) it relies on the payment ID alone and may stay amber. | Next: phrase sets for more Indian languages, so the rules alone also catch them. |
| **Bank account numbers cannot be checked.** We have no source for who owns an account. | A mule account written as account number + IFSC is not a strong signal by itself. | The ask and pattern still warn. Production: the bank's mule lists and RBI's MuleHunter.AI via Lookup. |
| **Live-registered videos last 24 hours.** | The jury's own clip is matched only during the demo day. | By design (consent and privacy). Production: institutions register their official channels permanently. |
| **"Check first" on genuine messages.** On our test split some genuine messages are told to check first (counted on the Lab, "What people are told"). | A genuine sender's customer is asked to confirm through the official number. | It never blocks; a bank removes it for its own messages by sealing them (green). |
| **The prompt-injection model is an extra, not the defence.** Prompt Guard 2 runs on Groq's free tier. | If it is down, hidden-instruction warnings rely on our own rules. | The real defence is structural: the AI never decides the verdict. |

## Measurement limits

- **The open dataset is not Indian traffic.** The 5,971-message public set (`eval/open_kit.py`) is mostly UK and US SMS. It shows our false-alarm rate on everyday messages and how far the rules carry outside India; it is not a measure of recall on Indian scams. The frozen first run is the headline; one later fix (bare-IP links) was found from its failure list and is disclosed on the Lab.
- **Small, self-built test set.** The test split holds 51 items, built by us from public advisories and consented clips. The public split holds 24 items quoted from news and advisories. The Lab shows 95% intervals, and they are wide. These numbers describe these sets, not the real world.
- **Frozen splits.**
  - The test split was frozen before the first measurement (`eval/FROZEN.sha256`).
  - The public split was frozen before any detection change (`eval/PUBLIC_FROZEN.sha256`).
  - Later items, such as the lip-sync fakes, went into the dev split only.
- **Synthetic rewrites.** The robustness rewrites were made by an AI model from frozen test scams. They show sensitivity to wording, not real-world recall.
- **Latency.** Measured on a laptop and on one small EC2 server. Text checks are near-instant when the AI answer is cached.

## Assumptions

- Users choose to forward a message; Pramaan sees nothing it isn't sent.
- In production the partner bank operates the service, owns the analyst Desk, and decides what to do with confirmed campaigns.
- Free AI tiers may keep inputs to improve their products. So the prototype only ever sends synthetic, public or consented content, with private numbers masked first.

## Risks we designed against

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for each risk, its control, and the test or red-team attack that checks it. See [docs/DATA_POLICY.md](docs/DATA_POLICY.md) for what is stored and for how long.
