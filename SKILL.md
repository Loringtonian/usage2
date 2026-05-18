---
name: usage2
description: For Claude Code SUBSCRIPTION users (Pro / Max 5x / Max 20x) — give the agent visibility into its own token consumption with API-equivalent dollar cost, % of session/week quota, and per-subagent attribution. Reads Claude Code's per-message `usage` blocks from the session transcript JSONL. Captures the built-in `/usage` panel via tmux for rolling 5h/7d/Sonnet-only quota percentages. Includes a passive calibration that learns your tier's tokens-per-percent from real samples. Use when the user says "/usage2", "how many tokens", "token cost", "compare token usage", "am I being efficient", "what's my quota", "how close to the limit", "subagent cost", "which subagent burned the most", or whenever the agent needs to reason about session/week budget, model efficiency, or A/B token comparisons.
allowed-tools: Bash
---

# /usage2

For **Claude subscription users** (Pro / Max 5x / Max 20x). The dollar figures are *API-equivalent* (what you would have paid on metered API). You actually pay the flat subscription fee.

Three capabilities in one skill:

1. **Token meter** (~10ms) — reads the session transcript JSONL and reports authoritative per-action token consumption, API-equivalent dollar cost, cache breakdown, per-subagent attribution.
2. **Quota panel** (~12s, cached for 10 min) — captures Claude Code's built-in `/usage` panel via tmux for rolling 5h / 7d / Sonnet-only meters with reset times.
3. **Calibration** — learns your tier's tokens-per-percent passively from each `quota` capture. After 2+ samples you can estimate "this 50K-token action will be ~X% of my session."

## First-time setup

```bash
python3 ${CLAUDE_SKILL_DIR}/meter.py tier max20x   # or pro / max5x
python3 ${CLAUDE_SKILL_DIR}/meter.py sample        # first calibration sample
```

(Sample again ~15 min later to derive slopes.)

## Invocation

```bash
python3 ${CLAUDE_SKILL_DIR}/meter.py [mode] [args]
```

Modes:

| Mode                | Purpose                                                          | Cost   |
|---------------------|------------------------------------------------------------------|--------|
| `summary` (default) | Tokens + $ + % session + % week + calibration + signals          | ~12s\* |
| `quick`             | One-line: tokens · $ · cache% · session% · week%                 | ~10ms  |
| `agents`            | Per-subagent attribution: agentType, $, prompt preview           | ~10ms  |
| `mark <name>` `[--quota]` | Save a checkpoint, optionally with a quota snapshot        | ~10ms / ~12s |
| `since <name>`      | Token + $ + quota delta since checkpoint                         | ~10ms  |
| `marks`             | List saved checkpoints                                           | ~1ms   |
| `drop <name>`       | Delete a checkpoint                                              | ~1ms   |
| `raw`               | JSON dump of everything (for downstream tools)                   | ~10ms  |
| `quota`             | Force-refresh quota panel + show parsed result                   | ~12s   |
| `sample`            | Take a calibration sample (forces quota capture)                 | ~12s   |
| `calibrate`         | Show calibration history + derived tokens-per-percent estimates  | ~1ms   |
| `calibrate-account-scope` | Consecutive-pair $/pp slopes from short-interval samples   | ~1ms   |
| `estimate` `--model <m> --tokens <N>` | $ + est. session/week % impact for a planned action | ~1ms   |
| `reset-calibration` | Archive all reports to `reports_archive/<timestamp>/`            | ~10ms  |
| `tier [<t>]`        | Show or set subscription tier (pro / max5x / max20x)             | ~1ms   |

\* The cached quota result is reused for 10 minutes, so consecutive `summary` calls within that window are ~10ms.

## A/B comparison workflow

For settling questions like *"native-resolution image vision request vs resize to 1024×1024 — which costs fewer tokens?"*:

```bash
python3 meter.py mark approach-A --quota
# ... agent does approach A ...
python3 meter.py since approach-A

python3 meter.py mark approach-B --quota
# ... agent does approach B ...
python3 meter.py since approach-B
```

`since` reports tokens + dollars + percentage-point delta on each quota window.

## Output anatomy

A full `summary` reports:

- **Main thread** — turns, tool calls, input/output/cache split, per-model breakdown, API-equivalent dollars, avg-per-turn
- **Subagents** — grouped by `agentType`, with assumed model (default mapping: Explore→Haiku, general-purpose→Sonnet), per-spawn dollars and prompt preview
- **Grand total** — tokens + dollars
- **Tier context** — "this session = N days of your subscription fee in API-equivalent value"
- **Rolling quota windows** — session 5h, week (all models), week (Sonnet only) with reset times, age of the cached reading
- **Calibration** — once you have ≥2 samples: tokens-per-percent and estimated full-window capacity
- **Efficiency signals** — cache hit ratio (good ≥80%, churning <50%), output/input ratio, per-turn growth trend

## Autonomous self-throttling

Tell the agent at the start of a long autonomous run:

> Every 10 minutes, run `/usage2 quick`. If session reaches 75% or grand-total grows by more than 500K tokens since the last check, pause and report. If cache hit ratio drops below 60%, also pause — something is invalidating the cache.

`quick` is ~10ms (uses cached quota). It's free to poll.

## How calibration works

Each time you run `sample` (or any mode that refreshes the quota panel), the meter records:

- Current %s for the three quota windows
- The trailing 5h and 7d token totals (weighted by API-rate ratios into "input-equivalent" units)

From ≥2 samples, the meter computes tokens-per-percent for each window. With this you can:

- See your tier's effective rolling-window capacity
- Estimate the % impact of a planned action before doing it
- Spot anomalies (a sudden jump in % with little token usage usually means the panel reset)

Anthropic doesn't publish exact per-tier token caps — calibration is how `usage2` learns them empirically.

## Caveats

- **The current in-flight turn isn't yet in the JSONL.** Claude Code writes assistant messages after the turn completes. The meter is always one turn behind.
- **Subagents are aggregated, not per-step.** `toolUseResult.totalTokens` gives the full cost of a subagent dispatch, but the parent transcript doesn't include the subagent's internal turn-by-turn detail. Subagent costs assume a model per `agentType` (see `AGENT_TYPE_MODEL` in `meter.py`).
- **Hooks aren't separately attributed.** PostToolUse / PreCompact hooks that inject context show up in the next assistant turn's input count, not as their own line.
- **Quota panel scrape spawns a real `claude` process.** No LLM tokens, but ~12s of latency. The 10-min cache amortizes this.
- **Subscription tier display vs reality.** The "days of subscription fee" line is informational — it doesn't represent your actual cost (which is the flat monthly fee), it represents the API-equivalent value of what you consumed.
- **API rates can shift.** `RATES` is hardcoded in `meter.py` — update when Anthropic publishes new pricing.

## Failure modes

- `ERR: no JSONL found for project slug '...'` — fresh project with no transcript yet, or CC's slug-naming convention drifted.
- `ERR: could not capture /usage panel` — see `capture.sh` for tmux scrape failure modes.
- Calibration estimates wrong/wild — too few samples, or all samples are within the same quota window since reset. Take more samples across longer time spans.
