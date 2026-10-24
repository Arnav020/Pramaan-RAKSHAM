# Pramaan spot-check kit

49 files a jury can run, change or replace. `expected.csv` gives each file's truth (`scam` or `genuine`) and where it
came from. Nothing in this folder was hand-picked: it is the whole of three sets.

| Set | Files | What |
|---|---|---|
| Public split | 24 `.txt` | Real scam messages and genuine official advisories as quoted in news reports and fact checks, each with its source URL in `expected.csv`. Frozen since 24 Oct (`eval/PUBLIC_FROZEN.sha256`). |
| Blind set | 10 `.txt` | Scam messages written by someone outside our rule-writing, run before any change made because of them. |
| Synthetic media | 15 | Screenshots, UPI QR posters, PDF notices (one scanned), voice notes in a synthetic voice, voice-over videos. Made by `eval/generate/make_stress_media.py`. No real person's face or voice. |

## Run it

```bash
python -m eval.spotcheck                  # this folder; prints a pass / fail table, writes eval/results/spotcheck.json
python -m eval.spotcheck path/to/folder   # any folder of your own .txt messages and media files
```

Or drop the files (and `expected.csv`, if you have one) on the site's **/kit** page.

## How a result is scored

What matters is what the person is told:

| Truth | PASS | soft (wrong, not an accusation) | HARD |
|---|---|---|---|
| scam | "Don't act": red, or "Warning signs of a scam" | "Check first" | Cleared |
| genuine | Anything except a warning | "Warning signs" | Marked red |

The kit's own targets are in `eval/gates.yaml`: no HARD errors and at least 90% PASS.

All names, UPI IDs and numbers in the synthetic files are fictional. The public-split texts are short quotes, each
attributed to its source.
