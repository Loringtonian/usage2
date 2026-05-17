# crowd_reports/

Anonymized usage observations contributed by /usage2 users.

Each file is a JSON bundle written by `usage2 contribute` (or its partner skill `/submit-usage2`). Contents per report:

- `tier` — subscription tier (`pro` / `max5x` / `max20x`)
- `timestamp_iso` — when the sample was taken (UTC)
- `pricing_snapshot_date` — date the meter's hardcoded API rates were verified
- `panel.session_pct` / `panel.week_all_pct` / `panel.week_sonnet_pct` — the /usage panel %s at that moment
- `trailing_5h` / `trailing_7d` — per-model token totals in the rolling window, plus weighted-input-equivalent and dollar value
- `anonymous_id` — UUIDv4 minted client-side; no personal info derives from it

What's **not** in a report: prompts, session content, file paths, usernames, emails, machine identifiers.

## Why this directory exists

Anthropic does not publish exact per-tier token caps, and the caps shift over time. `usage2` learns yours empirically via calibration. Pooling anonymized observations across users improves the model — especially when limits change — and helps people on a fresh install benefit from others' history before they've taken samples of their own.

## How to contribute

Easiest path: in any Claude Code session with `/usage2` installed, run:

```
/submit-usage2
```

That partner skill bundles your reports, forks this repo, opens a PR, and prints the URL. You confirm before any network call.

Manual path: run `python3 ~/.claude/skills/usage2/meter.py contribute > my-bundle.json`, fork this repo, drop the file at `crowd_reports/<your-anon-id>.json`, open a PR.

## How crowd data is used

`/usage2 summary --crowd` or `/usage2 calibrate --crowd` merges crowd reports into the local calibration. Reports are filtered to the calling user's tier (so a Max 20x user doesn't get skewed by Pro samples). Recency is preferred: when limits visibly shift (slope changes across recent batches of reports), the meter should age out older observations.
