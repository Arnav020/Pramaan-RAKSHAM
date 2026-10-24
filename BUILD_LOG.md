# Build log: what we built, when, and against which promise

The finale rules allow work before the event, and we say plainly which part was built when. The Round 1 deck's
48-hour plan is the yardstick for "Delivery against the Round 1 plan"; every row says what was built or why it was changed.

> Team review needed before submission: the finale section lists the work done on 24 Oct from 11:00 that is recorded
> in this repository. Add anything else either of us built at the venue.

## Timeline

| When | What |
|---|---|
| 12 Oct | Round 1 submitted (concept deck). |
| 17–20 Oct | Shortlisted (Top 30). Finale plan locked (`FINALE_PLAN.md`): eight approaches scored, Round 1 spine kept. |
| 21–23 Oct (before the event) | The working core: three lanes, fusion, Telegram bot, web check, proof pages, signed cards, evidence vault, campaigns and Desk, Lab and frozen evaluation, red-team suite, Seals, Lookup API, site visitor, EC2 deployment. |
| 24 Oct, 01:45–04:00 (before the event) | Overnight audit fixes (`AUDIT_PLAN.md`): rate limits, Desk sign-in by cookie and roles, appeals, public split frozen, deck. |
| **24 Oct, from 11:00 (finale)** | Self-audit against the Round 2 judging rubric, then the work below. |

## Built at the finale (24 Oct)

We scored ourselves against the judging rubric, attacked our own code, and fixed what we found. Each item has a test in
`tests/test_finale_hardening.py` or a row in the red-team suite.

| Area | What changed | Why (rubric line) |
|---|---|---|
| Fusion v2.2 | A matched scam pattern now blocks green, even when every link is official; a sealed text with a high-risk request goes to the analyst. | We found a scam that came back **green**: a typed SMS header plus a real bank link asking for a "KYC fee". (Adversarial robustness) |
| Lane A | An SMS header written inside the text is context, not proof of the sender. | Anyone can type "VM-SBIINB". (Adversarial robustness) |
| Playbooks | The OTP-scam pattern needs a request to share, not just the word "OTP". | A genuine OTP SMS was tagged as the OTP scam. (False positives) |
| New outcome | Amber with nothing to check and nothing risky is shown as "No warning signs", in neutral grey. | "Happy birthday" said "Can't verify", which reads as a warning. (Handling mistakes) |
| Pramaan Seal | The "unchanged" check now follows word order, and deleting a negation ("not", "never", "नहीं") counts as an edit. | Before, deleting "not" from a sealed notice still passed as "exactly what the bank sent". (Adversarial robustness) |
| Campaigns | Shared platforms (t.me, YouTube…), official bank domains and `@valid` IDs can never become scam indicators. | Confirming a campaign that mentioned t.me would have put a strong signal on every genuine Telegram link. (Adversarial robustness) |
| Evidence vault | Manifests are write-once (one file per chain record, opened with "x"); a second Report tap does not re-seal. | The vault promised write-once, but a second report overwrote the manifest. (Safeguards running) |
| Web endpoints | Report is rate-limited; malformed JSON is a 400; unknown campaigns are 404. | Abuse and error handling. |
| Privacy | The AI-answer and site caches are purged after 24 hours on the public server. | The data policy promised 24 hours; the cache kept quotes longer. (Privacy safeguards running) |
| Site visitor | The address actually connected to is checked again (HTTP client and browser): DNS rebinding cannot reach the cloud metadata service. | Server-side request forgery hardening. |
| Appeals | Proof page "This looks wrong" button (it was promised but missing); the analyst's answer goes back to the person on Telegram; an upheld verdict is marked "Corrected" on its proof and verify pages; median wait shown on the Lab. | "What a genuine user flagged by mistake goes through, and how fast". (Responsible AI and mistakes) |
| Listen | The proof page reads the verdict aloud with the phone's own voice (on-device speech; nothing is sent). | Round 1 promised voice for low-literacy users. (Delivery vs plan) |
| Live Source-Match | Issuer console: register a short official video live (consent required, deleted after 24 h), make a labelled test edit with a new recording, and forward it. | The jury can vary the input: Source-Match on their own footage, not only our three clips. (Working prototype, Live user journey) |
| Metrics | Every verdict is also scored by what the person is told (Warn / Check first / Clear), so false alarms on genuine messages are counted, not only false reds. | Measured accuracy including false positives. (Spot-check results) |
| Open dataset | `eval/open_kit.py`: all 5,971 messages of a public CC BY 4.0 SMS phishing dataset, hash-checked, never used for tuning. | "Error rates on data the model never trained on". (Spot-check results) |
| Red team | Three audit attacks added, plus a **Known misses** group: four attacks that still beat us, run every time, each with what we would change. | "Shows how an attacker would evade it, what it misses now". (Adversarial robustness) |
| Test kit | `python -m eval.kit` runs tests, both evaluations, the red team and robustness, and writes a coverage summary; `eval/REPRODUCE.md`. | Organisers: the test workflow and reproducibility are part of the submission. |
| Stress test (night) | `eval/stress.py` runs 71 "what would a judge type?" inputs (`eval/stress_set.yaml`: text in English, Hindi and Hinglish, plus screenshots, QR posters, PDFs including a scanned one, voice notes and videos, made by `eval/generate/make_stress_media.py`). First run: 0 genuine messages red, 0 scams cleared, but 17 of 38 clear text scams only told "check first". | The demo must survive any input the jury picks. (Live user journey, Working prototype) |
| Rules from the stress test | Asks for card, bank, PAN or Aadhaar details and "the verification code" count as credential requests; Hinglish/Hindi payment requests ("bhejo", "रुपये भेज दो"), "return it to …", "send 5k"; deadline cut-offs as threats; "turn ₹X into ₹Y" and pay-a-fee-to-release as promises; spaced-out letters ("O T P") and links broken across lines are rejoined; a call to a personal mobile inside a known scam script counts as high risk; "don't call me" is a scammer's order, not advice. | Each was a general phrasing gap, not a patch for one sentence. Pinned in `tests/test_stress_rules.py`. |
| Government senders | Income Tax, Parivahan/e-Challan, NHAI/FASTag, India Post and UIDAI added to the registry (domains DNS-checked). A message in a government body's name that links outside gov.in / nic.in is a strong contradiction; any gov.in / nic.in address is recognised as government and never as a look-alike. | Government sites are only on gov.in / nic.in; scams use look-alike .com/.in sites. |
| Playbooks 15 → 19 | 'Hi Mum, new number' family impersonation; 'sent by mistake, return it'; gift from abroad held at customs; job that asks for a fee. Each cites its public source. | Common Indian scripts the stress test found uncovered. |
| High-risk title | Amber with a high-risk request now reads "Warning signs of a scam. Don't pay or share anything" instead of "Can't verify". Not an accusation (one lane only), but no longer reads as a shrug. | (Alert a person can act on) |
| AI tag check | An AI "guaranteed returns" tag is kept only if its quote talks about money growing. | With the AI on, "kal lauta dunga" (I'll pay you back tomorrow) was tagged as a returns promise and a friend's request got a warning. |
| After the changes | Stress set 70/71 with the AI on, 0 hard errors (the one soft result is by design: a genuine voice note is "check first", because media we analysed is never "nothing to verify"); hold-out set written after the fixes (`eval/stress_holdout.yaml`, 28 items): 25/28 before its three gaps were fixed, 28/28 after. Frozen test split: scams warned 23 → 24 of 26, genuine warned still 0. Public split: 1 scam now red, genuine warned still 0. Open dataset: legitimate warned 1 → 2 of 4,844 (0 red). Red team 34/34. 174 tests. | **Both stress sets are tuned on and self-written: they are dev checks, never reported as accuracy.** |

## Round 1 48-hour plan → what exists

| Promised in Round 1 | Status | Notes |
|---|---|---|
| Telegram bot + web upload | **Built** | Four action buttons, "On a call" mode, family and appeal. Web check, share target. |
| WhatsApp (if time allows) | **Not built** | The WhatsApp Business test number needs business verification we cannot get as students. Same engine; only the channel differs. |
| Lane A: SEBI list, `@valid`, `.bank.in`, 1600/140, DLT, Alert List | **Built** | 24 banks, 95 Alert List entries. `@valid` is checked by pattern (SEBI Check has no public API). DLT headers in pasted text are context only (finale change). |
| DoT FRI | **Mocked, labelled MOCK** | Only regulated entities can query it. |
| Source-Match with 5 stand-in figures | **Changed: 3 consented clips + live registration** | Two team members gave written consent; we did not invent more personas. At the finale, any consenting person's clip can be registered live. |
| One video detector, one audio detector | **Built, shown to fail on their own** | GenD (video) and a wav2vec2 voice model. The Lab's key experiment shows why they stay weak hints. |
| LLM extractor + playbook RAG (Hindi/English) | **Changed: LLM extractor + 19 rule playbooks + local multilingual semantic matcher** | Rules are auditable and need no AI service to decide; the matcher adds rewording robustness. A RAG answer would be one more model output to trust. |
| Fusion + reason codes | **Built** | Rule table v2.2, versioned. |
| Hindi card and voice | **Built** | Cards and reasons in Hindi and English. Voice: on-device "Listen" (finale) instead of a server voice service: no cost, nothing leaves the phone. |
| Golden-Hour PDF | **Changed: ready-to-paste drafts + text packet** | The NCRP portal and 1930 take typed details; a PDF added nothing a person could use. |
| Evidence vault with hashes | **Built** | Write-once objects, hash chain, signed manifests. |
| Analyst console with clustering | **Built (campaigns by shared hard indicators)** | Grouping by the same UPI ID, number, domain or re-used footage is explainable to an analyst; perceptual-hash clustering of images was not needed for the demo items. |
| Metrics board on a 30-item set | **Built, larger** | 51 frozen test items, 18 dev, 24 public, plus the 5,971-message open dataset (finale). |
| Demo 1: genuine `.bank.in` notice → green | **Works** | |
| Demo 2: re-voiced "public figure" video + non-`@valid` UPI → red, original side by side | **Works** | With consented team clips, and live with a judge's own clip. |
| Demo 3: forged "RBI" PDF → red | **Works** | Borrowed circular number, PDF forensics. |
| Demo 4: blurry genuine video a detector flags → no false accusation | **Works** | The detector flags it; Pramaan does not go red. |

Beyond the plan (built before the event): Pramaan Seal for issuers, Lookup API and a mock pay-time pause, Retro-Alerts,
the safe website visitor, the red-team page, appeals.

## Prototype vs production

See the README's "Prototype vs production" table: one EC2 instance and free AI tiers now; the Round 1 AWS design
(API Gateway, Step Functions, Bedrock, S3 Object Lock, KMS) for a bank pilot.
