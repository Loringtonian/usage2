# Per-model cost vs. panel-% — v2 (Max 20x, 2026-05-18)

**Supersedes:** [`per_model_cost.md`](per_model_cost.md). The v1 numbers ($1.71/pp session, $171 cap) were derived from a flawed methodology that this experiment exposed.

**TL;DR:** A controlled re-run with foreground subagent dispatch found that **per-model isolation via the Agent tool is structurally impossible** for /usage2 attribution, because subagent returns become parent-thread `cache_write_1h` at Opus rates. Even with foreground dispatch (which makes `toolUseResult.totalTokens` visible to the meter), the parent's cache writes from agent returns dominate the actual quota cost. Both Stage 1 (Haiku-targeted) and Stage 2 (Sonnet-targeted) were 97-98% Opus-dominated. The per-model multiplier hypothesis (H2) **cannot be tested by any subagent-based protocol**. New empirical caps below.

---

## Headline numbers (Max 20x, 2026-05-18)

| Stage | Target model | Actual dominance | Δsession | API-$ | $/pp |
|---|---|---|---|---|---|
| v2 Stage 1 (Haiku × 16 foreground) | Haiku | **Opus 97.7%** / Haiku 2.3% | 3pp | $21.91 | **$7.30** |
| v2 Stage 2 (Sonnet × 2 foreground) | Sonnet | **Opus 98.1%** / Sonnet 1.9% | 1pp | $3.37 | **$3.37** |
| v2 Stage 3 (Opus, main thread) | Opus | TBD post-write | TBD | TBD | TBD |

The cross-stage variance ($3.37–$7.30/pp) is large but does **not** reflect per-model differences — all stages were effectively Opus by cost, even when designed to be Haiku or Sonnet.

---

## What the experiment was supposed to test

**H1 (null):** Anthropic's panel %s linear in API-equivalent dollars, regardless of model. Implication: meter calibration is correct.

**H2:** Panel weights differ per model (e.g., Sonnet penalized, Haiku discounted). Implication: meter needs per-model multipliers.

**What we found instead:** A third hypothesis, unstated initially:

**H3 (the methodology):** *Subagent dispatch via the Claude Code Agent tool causes the parent thread to absorb most of the API-equivalent cost via `cache_write_1h`, regardless of which model the subagent runs.* The intended model can never dominate because the orchestration is always Opus.

H3 holds robustly across both stages.

---

## Why subagent-based per-model testing fails

When the Claude Code agent dispatches a foreground subagent via the Agent tool:

1. The subagent runs its model (Haiku for Explore, Sonnet for general-purpose).
2. The subagent returns a multi-thousand-token markdown report to the parent.
3. The parent's next turn ingests that report as **input** to its own next turn.
4. Claude Code's caching layer writes that report into the parent's `cache_write_1h` (durable 1-hour cache) at the parent's model rate — **Opus**.
5. Future parent turns replay it as `cache_read` (also Opus rate).

For this experiment, Stage 1 (16 Haiku subagents) generated returns totaling ~1.3M `cache_write_1h` tokens at Opus rate $10/M = **$13.10 of parent-side Opus cost** for caching Haiku output. The Haiku subagents themselves cost $0.49. **The parent cache cost was 27× the subagent's own cost.**

This is fundamental: any work that produces output flowing back to a long-running Claude Code session becomes Opus cache_write cost on the parent side. The subagent's own model is largely irrelevant to the API-equivalent total.

---

## Methodology

**Conditions.** Single-instance Max 20x. Session reset 4:20pm Berlin; new window started 11:20am, session at 18% baseline. No other agents running (verified by `ps aux` + `find -mmin -1`). Active concurrent = $0 (historical residual = $145.93).

**v2 stages:**

1. **Stage 1 — 16 foreground Explore (Haiku) agents.** Each given a substantial audit/code-trace task on the usage2 repo (5K+ token reports expected). 4 dispatched sequentially, 12 in parallel via one tool_calls block. All used foreground dispatch so `toolUseResult.totalTokens` lands in the parent JSONL (fixing v1's invisibility issue).

2. **Stage 2 — 2 foreground general-purpose (Sonnet) agents.** Each implemented real fixes flagged by Stage 1: min-delta calibrate guards (the bug that produced $300/pp weekly estimates) and mode/doc parity (removed ghost `summary-fast`, added 3 missing modes to SKILL.md).

3. **Stage 3 — main-thread Opus synthesis.** Writing this document, syncing skill→repo, etc.

**Per-stage measurement:** `/usage2 sample` before/after, plus a direct JSONL scan over the time window summing per-record `cost_of()` by model.

---

## Detailed Stage-1 cost breakdown

For the 17.8 min between T0 (17:23 Berlin) and T1 (17:41), account-scope trailing_5h grew by $21.91. JSONL scan attributed this as:

| Model | Records | Input | Output | Cache_read | Cache_write_1h | $ |
|---|---|---|---|---|---|---|
| Opus 4.7 (main thread) | 37 | 117 | 114,854 | 10,884,757 | 1,310,441 | **$21.42** |
| Haiku 4.5 (16 Explore subagents) | 16 | 66 | 53,738 | 773,142 | 0 | **$0.49** |

Opus breakdown by bucket:
- input: $0.0006 (negligible)
- output: $2.87 (13%)
- cache_read: $5.44 (25%)
- **cache_write_1h: $13.10 (61%)**

That 61% cache_write_1h is the subagent returns being cached into the parent's context.

---

## What this means for the v1 caps

The v1 research claimed Max 20x session 5h cap = $171, derived from 15 chronological pair-deltas across 41 reports via `account_scope_slopes()`. That method used `trailing_7d_$` delta as a proxy for "dollars spent" over short intervals.

**Two problems with v1's caps**, exposed by v2:

1. **v1's pair-deltas weren't measuring intense single-instance burn.** They averaged across 41 reports, many during periods of moderate concurrent activity. The current v2 measurement of $7.30/pp under intense single-instance Opus orchestration suggests the panel weights cache writes more heavily than the prior calibration implied — OR the panel is non-linear in API-$.

2. **v1 missed the cache_write_1h component.** During v1, the experiment moved only 4pp total across 3 stages — barely above panel resolution. The $1.71/pp number was a fragile average over a varied workload.

**Provisional v2 caps** (acknowledging the noise):

| Window | v1 estimate ($171/$1185/$811) | v2 observation | Confidence |
|---|---|---|---|
| Session 5h | $171 | **$337–$730** range, midpoint ~$500 | Low (1pp + 3pp samples, both Opus-dominated) |
| Week (all) 7d | $1,185 | TBD (week_all moved 1pp Stage 2 only) | Very low |
| Week (Sonnet) 7d | $811 | TBD (week_sonnet didn't move in v2) | None |

**Recommendation:** Use the **higher** v2 caps as defensive planning numbers until more data is collected. The v1 caps may have been overoptimistic.

---

## The min-delta calibrate guards (shipped in v2 Stage 2)

The v1 `calibrate` mode produced wildly inflated weekly estimates after the experiment ($300/pp = $30,012 cap for week_all). Root cause: 4 clean reports spanning a few minutes, weekly panel barely moved, slope = noise.

**Fix shipped:** New `slope_with_fallback()` wrapper in meter.py applies per-window guards and auto-defers to `account_scope_slopes()` when:
- Clean sample count < 5, OR
- Panel-Δ span across clean samples < 5pp

Plus per-pair guards in `slope_from_reports()`:
- `delta_pp >= 1` (no sub-1pp noise pairs)
- `delta_dollars > 0` (already enforced)
- `time_span_seconds >= 60` (no same-minute duplicates)

`cmd_calibrate` now displays: `(insufficient clean data; using account-scope: <reason>)` with the fallback number.

**Verification:** `meter.py calibrate` now produces sensible numbers ($1.71-$11.85/pp) instead of $300/pp. Week_all dropped from $30,012 cap → $1,185 cap.

---

## Other v2 fixes shipped

- **Mode/doc parity** — removed ghost `summary-fast` row from both SKILL.md files. Added `calibrate-account-scope`, `estimate`, `reset-calibration` rows that were in code but missing from docs.
- **meter.py docstring sync** — added the 3 modes to the docstring at the top of the file.

---

## What still needs to be tested (and how, given v2's lesson)

The H1 vs H2 question — does Anthropic weight per-model? — **cannot be answered with subagent dispatch protocols**. Possible alternative tests:

1. **Three separate Claude Code sessions, one per model.** Use the model selector in CC settings. Run a comparable workload in each, sample panel %s. Compare $/pp across the three sessions. This avoids the subagent-cache problem because there's no parent absorbing returns.

2. **Direct API calls (if you have an API key).** Not available on subscription-only setups.

3. **Process-level instrumentation.** Read JSONLs from multiple concurrent CC sessions running different models for the same task. More complex orchestration; outside the scope of `/usage2`.

For now, H1 vs H2 remains untested. The meter's calibration in `calibrate-account-scope` mode produces a single $/pp number against API-equivalent dollars; this number is the best available estimate, but its accuracy is uncertain.

---

## Methodology limitations (carried forward from v1)

- **Subagent attribution: foreground only.** Background subagents (`run_in_background: true`) leave no trace in the parent JSONL. v2 confirmed this by using only foreground dispatch.
- **Panel 1pp resolution.** Stage 2 moved only 1pp. $/pp at that resolution has ±50% noise.
- **5h back-edge erosion.** For samples >5 min apart, work falling off the back-edge biases trailing_5h-as-proxy. v2 cross-checks against direct JSONL scan.
- **One tier, one day.** Max 20x on 2026-05-18 only.

---

## Reproducing this

```bash
# Single-instance, clean conditions
python3 ~/.claude/skills/usage2/meter.py tier max20x
python3 ~/.claude/skills/usage2/meter.py sample

# Foreground subagent dispatch (the Agent tool in Claude Code, without --run-in-background)
# ... do real work ...

python3 ~/.claude/skills/usage2/meter.py sample

# Analyze (uses the new slope_with_fallback)
python3 ~/.claude/skills/usage2/meter.py calibrate

# Or the unbiased account-scope:
python3 ~/.claude/skills/usage2/meter.py calibrate-account-scope
```

---

## Raw data

`experiment_2026-05-18_v2.json` in this directory.

## License

MIT.
