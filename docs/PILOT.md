# A 12-week pilot with one bank

What it would take to run Pramaan for real customers of one bank. Every cost below is an **estimate**, not a quote or a
measurement; every target is a **target**.

## Who runs it

| Role | Who | What they do |
|---|---|---|
| Operating owner | The partner bank's **Fraud Risk Management (FRM) unit**, under the CISO | Owns the service, the analyst Desk and every decision on a confirmed campaign |
| Analysts | 1 FRM analyst, about a quarter of their time | Confirm or reject campaigns (once each), answer appeals |
| Issuer | The bank's communications team | Seals outgoing notices and SMS with the bank's own key |
| Engineering | The bank's IT, with us during the pilot | Runs the stack in the bank's AWS account in Mumbai |
| Escalation | 1930 / NCRP, through the bank's existing reporting | Pramaan prepares packets; people send them |

Pramaan never blocks a payment. The bank's own controls stay in charge.

## What customers get

1. **Forward-to-verify**: the bank's verified WhatsApp number (or Telegram during the pilot) and an in-app "Share to check".
2. **The pay-time pause**: the bank's app calls the Lookup API when a new payee is added; a payee in a campaign the bank's
   analysts confirmed shows a pause screen. Nothing else changes in the app.
3. **Sealed messages**: the bank's own notices and SMS verify as green, so customers learn what genuine looks like.

## What the pilot needs from the bank

- A sandbox AWS account in `ap-south-1`, and the bank's security review of the design (`docs/THREAT_MODEL.md`, `docs/DATA_POLICY.md`).
- A list of the bank's official domains, numbers, SMS headers and `@valid` UPI handles (Lane A's own rails for that bank).
- Read access to the bank's internal mule-account and fraud-reported VPA lists (makes the Lookup API useful from day 1).
- DoT FRI access through the bank (replaces our mock).
- A named FRM analyst, and a 30-minute weekly review with us.
- A cohort of **10,000 opted-in customers**, with priority for first-time investors and customers aged 45+.

## Stack in production (Round 1 design)

API Gateway + WAF + Cognito → Step Functions → Lambda / Fargate (ffmpeg) → Amazon Bedrock (Claude, with Guardrails) for
extraction only → rule fusion in Lambda → S3 Object Lock + KMS evidence vault → DynamoDB → the Desk on Amplify.
Everything stays in Mumbai; inputs are not used for training.

## Cost (estimates)

Assumes 10,000 users, about 2 checks per user per month (20,000 checks), 90% text and 10% video.

| Item | Estimate per month |
|---|---|
| Text checks: 18,000 × ≈ ₹0.2 | ≈ ₹3,600 |
| Video and voice checks: 2,000 × ≈ ₹4 | ≈ ₹8,000 |
| Always-on base (WAF rules, KMS keys, DynamoDB, S3 Object Lock, logs; API Gateway is per request) | ≈ ₹2,000–5,000 |
| **Cloud total** | **≈ ₹14,000–17,000** (under ₹2 per user per month) |
| Analyst time (about 0.25 FTE) | the bank's existing FRM team |

For scale: I4C reported ₹11,333 crore lost to cyber fraud in India from Jan to Sep 2024, the largest share to stock-trading and
investment scams. One prevented ₹50,000 loss covers more than two years of this pilot's cloud cost (estimate).

## Timeline

| Weeks | What happens |
|---|---|
| 1–2 | Security review; deploy to the bank's sandbox; load the bank's rails; seal its first notices |
| 3–4 | Staff-only trial (FRM and branch staff forward real suspicious messages they receive) |
| 5–10 | 10,000 opted-in customers; weekly rule review with the analyst; appeals answered within one working day |
| 11–12 | Evaluation against the success criteria; go / no-go for a wider rollout |

## Success criteria (targets, agreed before the pilot starts)

| Measure | Target |
|---|---|
| Genuine bank messages marked red | 0 |
| Red verdicts that were scams | ≥ 95% |
| Appeals upheld (verdict was wrong) | reviewed weekly; every upheld appeal becomes a rule change or a test |
| Median time to answer an appeal | < 1 working day |
| p95 time to a verdict, text / 60-second video | < 5 s / < 25 s |
| Customers who reported a scam and were told the outcome | 100% (Retro-Alerts) |
| Payments paused at pay time that the customer then cancelled | counted and reported (no target: we do not know the base rate) |

## After the pilot

P1: in-app Share and the pay-time pause for all customers. P2: a shared campaign feed across banks, I4C and SEBI
(one bank's confirmation protects every bank's customers). P3: banks sign their communications with C2PA content credentials.
