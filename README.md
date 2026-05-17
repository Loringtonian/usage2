# usage2

A Claude Code skill that gives the agent **visibility into its own token consumption** — token counts, API-equivalent dollar cost, percentage of rolling 5h/7d quota windows, per-subagent attribution, and an A/B comparison workflow.

**For Claude Code subscription users** (Pro / Max 5x / Max 20x). The dollar figures it reports are *API-equivalent* — what the same tokens would have cost on metered API access. You actually pay the flat monthly subscription fee. The skill is built around making that gap visible: how much value you're extracting, where it's going, and how close you are to your tier's hard limits.

Every quota capture writes a timestamped report to `reports/`. Subsequent runs read the whole history to learn your tier's tokens-per-percent empirically. You can crowdsource calibration via the partner skill **`/submit-usage2`** — it opens a PR adding your anonymized bundle to `crowd_reports/`. Other users pull from `crowd_reports/` to bootstrap their own calibration.

```
### Main thread
  Turns:              227
  Tool calls issued:  128
  Input (fresh):      121.9K
  Output:             366.5K
  Cache read:         29.40M  (94% of all input)
  Cache write:        1.60M
  Main-thread total:  31.49M  ·  $121 API-equiv

### Subagents (3 spawns, 187.9K tokens, $0.044)
  Explore × 3 (assumed claude-haiku-4-5): 187.9K · $0.044
    └─ 52.0K   $0.012  in 6  out 1.9K  cache-r 49.7K  45.4s  16 tools  "Audit branch ship-readiness…"
    └─ 61.9K   $0.012  in 4  out 1.9K  cache-r 59.9K  66.1s  30 tools  "Plan a fix for two broken hooks…"
    └─ 74.0K   $0.019  in 2  out 3.4K  cache-r 70.4K  83.2s  21 tools  "Plan a Fuller deep dive…"

### Grand total: 31.68M tokens · $121 API-equiv
### Tier: Max 20x ($200/mo)  →  this session = 18.2 days of your subscription fee in API-equivalent value

### Rolling quota windows (from /usage panel)
  Session (5h window):   98%   resets 8:10pm (Europe/Berlin)
  Week (all models):     66%   resets May 18 at 3am (Europe/Berlin)
  Week (Sonnet only):    58%   resets May 18 at 2:59am (Europe/Berlin)

### Calibration (2 samples in history)
  Session 5h capacity:    ~4.4M weighted-input-equiv tokens  (44.4K per 1%)
```

## Why this exists

Until now, Claude Code has been blind to its own resource use. Ask the agent "am I being token-efficient?" or "did approach A cost more than B?" and the honest answer is: *"I have no idea — I can't see my own usage."* Claude Code paints a `/usage` panel into your terminal, but the agent can't see TUI panels. The per-subagent cost is hidden. The cache hit ratio is hidden. The per-turn growth trajectory is hidden. The agent has been flying blind on its own resource use.

`usage2` fixes that by reading three signals already on disk:

1. **Session transcript JSONL** — Claude Code writes every assistant turn's `usage` block (input, output, cache_read, cache_write, model) to `~/.claude/projects/<slug>/<session>.jsonl`. Sum it for authoritative per-action attribution. ~10ms.
2. **`/usage` panel** — captured via a tmux-driven scrape of a throwaway `claude` instance. Returns the rolling 5h / 7d / Sonnet-only quota percentages with reset times. Cached for 10 minutes. ~12s.
3. **Calibration history** — every quota capture is paired with the trailing-window token total, so `usage2` passively learns your tier's tokens-per-percent over time. After ≥2 samples it can estimate "this 50K-token action will be ~1% of your session."

## Who this is for

People doing **long autonomous work with Claude Code on a subscription** who want the agent itself to be able to:

- **Reason about token efficiency in real time** — "I burned 50K tokens on that subagent; was it worth it? Where did the spend actually go — input, output, or cache writes?"
- **Run A/B token experiments** — "Does this image at native resolution actually cost more tokens than resized to 1024×1024? Mark, run both, check the deltas."
- **Self-throttle on long runs** — "Every 10 min, run `/usage2 quick`. Pause when session reaches 75% or cache hit drops below 60%."
- **Attribute the cost back to the choice** — *which* subagent dispatch, with which prompt, burned how many tokens of which model.
- **See the value being extracted** — "Today's session was worth $121 in API-equivalent — I'm getting 18× my subscription fee back on this run alone."
- **Know when to stop** — the agent can see its own % of session and week, with reset times, and decide whether to push through or hand back.

This turns quota and token cost from external constraints the user has to babysit into signals the agent can reason about on its own.

## Subscription tiers

| Tier      | Monthly | Notes                                                       |
|-----------|---------|-------------------------------------------------------------|
| Pro       | $20     | Entry tier                                                  |
| Max 5x    | $100    | 5× Pro's quota envelope                                     |
| Max 20x   | $200    | 20× Pro's quota envelope                                    |

Anthropic doesn't publish exact per-tier token caps — `usage2` learns them empirically via calibration. Set your tier so the "% of subscription value" line is meaningful:

```bash
python3 meter.py tier max20x   # or max5x, or pro
```

This skill is **not for API users** — if you're metered, your own billing page is more accurate. The point here is bridging the visibility gap that subscription pricing creates.

## What it consists of

- **`meter.py`** — Python script (stdlib only). All the modes below.
- **`capture.sh`** — Bash + tmux that captures the built-in `/usage` panel (the original v1 of this skill).
- **`SKILL.md`** — Claude Code skill descriptor.

No npm, no Python deps, no API keys.

## Modes

| Mode                      | Purpose                                                            | Cost   |
|---------------------------|--------------------------------------------------------------------|--------|
| `summary` (default)       | Tokens + $ + % session + % week + calibration + signals            | ~12s\* |
| `summary-fast`            | Same but skip quota refresh (use cached %)                         | ~10ms  |
| `quick`                   | One-line: tokens · $ · cache% · session% · week%                   | ~10ms  |
| `agents`                  | Per-subagent attribution                                           | ~10ms  |
| `mark <name>` `[--quota]` | Save a checkpoint, optionally with a quota snapshot                | ~10ms / ~12s |
| `since <name>`            | Tokens + $ + quota Δ since checkpoint                              | ~10ms  |
| `marks`                   | List saved checkpoints                                             | ~1ms   |
| `drop <name>`             | Delete a checkpoint                                                | ~1ms   |
| `raw`                     | JSON dump of everything (for downstream tools)                     | ~10ms  |
| `quota`                   | Force-refresh quota panel + show parsed result                     | ~12s   |
| `sample`                  | Record a calibration sample                                        | ~12s   |
| `calibrate`               | Show calibration history + derived tokens-per-percent              | ~1ms   |
| `tier [<t>]`              | Show or set subscription tier (pro / max5x / max20x)               | ~1ms   |

\* `summary` calls within 10 min of a previous quota capture reuse the cached % and run in ~10ms.

## The A/B workflow

```bash
python3 meter.py mark approach-A --quota
# ... agent does approach A ...
python3 meter.py since approach-A

python3 meter.py mark approach-B --quota
# ... agent does approach B ...
python3 meter.py since approach-B
```

Each `since` reports the tokens consumed, the API-equivalent dollar delta, and (because `--quota` snapshot was saved at mark time) the percentage-point movement on each rolling window. The agent compares the two deltas and tells you which approach is cheaper, by what margin, and where the difference lives (input? output? cache?).

## Calibration: learning your tier empirically

Anthropic doesn't publish per-tier token caps. So `usage2` learns yours:

```bash
python3 meter.py sample              # take a sample now
# ... ~15+ min and some real usage later ...
python3 meter.py sample              # take another
python3 meter.py calibrate           # show derived estimates
```

Each sample stores `(quota %, trailing-5h tokens, trailing-7d tokens)`. With ≥2 samples the meter derives tokens-per-percent via consecutive-pair slopes (median-of-slopes for robustness). Estimates feed into `summary`'s capacity readouts.

## Autonomous self-throttling pattern

Tell the agent at the start of a long run:

> Every 10 minutes, run `/usage2 quick`. If session reaches 75% or grand-total grows by more than 500K tokens since the last check, pause and report. If cache hit ratio drops below 60%, pause — context is churning.

`quick` is ~10ms with cached quota.

## Comparison to similar approaches

- **`claude -p` `total_cost_usd`** — only for subprocess (non-interactive) runs; one number per invocation; no breakdown.
- **`anthropic.count_tokens()`** — pre-counts input tokens for a planned API call; requires an API key (not available on Pro Max); input-only.
- **OpenTelemetry** (`CLAUDE_CODE_ENABLE_TELEMETRY=1`) — Anthropic's official structured-metrics path; more powerful but requires a collector (Prometheus / Grafana / etc.).

`usage2` is the zero-setup alternative for subscription users.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on `$PATH`
- Python 3.10+ (stdlib only)
- `tmux` — `brew install tmux` (only needed for quota / sample / `summary` with stale cache)
- bash 3+ (macOS default works)

## Install

```bash
git clone https://github.com/Loringtonian/usage2.git
mkdir -p ~/.claude/skills
cp -r usage2 ~/.claude/skills/usage2
chmod +x ~/.claude/skills/usage2/capture.sh ~/.claude/skills/usage2/meter.py
python3 ~/.claude/skills/usage2/meter.py tier max20x   # set your tier
python3 ~/.claude/skills/usage2/meter.py sample        # first calibration sample
```

In any Claude Code session, type `/usage2` (or just ask: "how many tokens has this session burned?", "compare approach A vs B token-wise", "how close to my session limit am I?", etc.).

## How it works (technical)

1. **Project slug** from cwd: leading `/` → `-`, all `/` → `-`, all `_` → `-`. (Claude Code's naming convention.)
2. **Current session** = most-recently-modified `*.jsonl` in `~/.claude/projects/<slug>/`.
3. **Transcript scan** — stream the JSONL. Each `type:"assistant"` record (with `isSidechain` falsy) contributes `message.usage`; each `type:"user"` record with `toolUseResult.agentType` is a subagent dispatch contributing `totalTokens` + `usage`.
4. **Per-model rates** — `RATES` dict in `meter.py` mirrors Anthropic's public API pricing. Each turn's cost is computed against its declared model. Subagent costs use a per-`agentType` model assumption.
5. **Quota capture** — `capture.sh` spawns `claude` in a detached tmux session, dismisses the trust prompt, sends `/usage`, captures the rendered panel via `tmux capture-pane -p`. `meter.py` parses the percentages and reset times.
6. **Calibration** — each quota capture is paired with a transcript-derived trailing-window token total. The history file accumulates samples; `tokens-per-percent` is the median of consecutive-pair slopes.

## Caveats

- **The in-flight turn isn't in the JSONL yet.** The meter is always one turn behind the live state. Fine for between-turn polling.
- **Subagents are aggregated.** You see the full cost of each spawn, not its internal turn-by-turn breakdown.
- **Hooks aren't separately attributed.** Context they inject shows up in the next assistant turn's input count.
- **The "days of subscription fee" line is informational** — it represents API-equivalent value extracted, not what you actually pay.
- **Calibration needs ≥2 samples** spread across enough usage to see real movement. Anthropic's quota windows reset on a rolling basis; samples too close together may give noisy slopes.
- **API rates can shift.** `RATES` is hardcoded; update when Anthropic publishes new pricing.

## License

MIT. See `LICENSE`.

## Background

Built in May 2026 trying to settle a token-efficiency question on a Personal Media Archive project (biographical-detail subagents on photos: native-res vs 1024×1024?) and discovering the agent had no way to answer. v1 was a tmux scrape of the built-in `/usage` panel. v2 added per-action token attribution from the session transcript. v3 (current) adds API-equivalent dollar cost, per-tier framing, and passive calibration. The trick — that Claude Code already writes authoritative per-message usage blocks to local JSONL files — generalizes: any "give the agent visibility into something it currently can't see in Claude Code" problem probably has a similar local-file answer.
