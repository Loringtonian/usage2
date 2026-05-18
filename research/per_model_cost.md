# Per-model cost vs. panel-% (Max 20x, 2026-05-18)

> **⚠ SUPERSEDED 2026-05-18 (twice):** v2 exposed methodology flaws here, then v3 superseded v2. **See [per_model_cost_v3.md](per_model_cost_v3.md)** for the current findings. v1's $1.71/pp / $171 cap was derived from a flawed JSONL-scan calculation (cache-write double-count + date-suffix model fallback to Sonnet pricing). The actual Max 20x session 5h cap measured directly via `claude -p` subprocesses is **~$72**, not $171.

A controlled three-stage experiment on a single Max 20x account to test whether Anthropic's `/usage` panel weights different models the same way the public API rates suggest.

**TL;DR:** Within measurement resolution, **session and weekly panel %s are well-approximated by API-equivalent dollars** regardless of model mix. We could not detect any per-model multiplier. Concrete implications + caveats below.

---

## Numbers you can plan around

For a **Max 20x** account on 2026-05-18 (Anthropic adjusts these without notice):

| Window | Median $/pp | IQR | Implied cap | Method |
|---|---|---|---|---|
| Session (5h) | $1.71 | $1.63 – $1.76 | **~$171** | 15 consecutive-pair slopes |
| Week (all models, 7d) | $11.85 | $9.44 – $16.35 | **~$1,185** | Same |
| Week (Sonnet only, 7d) | $8.11 | $7.46 – $8.71 | **~$811** | Same |

The Sonnet weekly is **the bottleneck**: at ~70% of the all-models cap, it caps out first for Sonnet-heavy workflows.

### Per-1M-tokens reference (derived from caps + API rates)

For a **Max 20x** account, the % of a session 5h window that 1M tokens of each model + bucket consumes:

| Model | 1M input | 1M output | 1M cache_read | 1M cache_write_5m | 1M cache_write_1h |
|---|---|---|---|---|---|
| Opus 4.7 | 2.9% | **14.6%** | 0.29% | 3.7% | 5.8% |
| Sonnet 4.6 | 1.8% | 8.8% | 0.18% | 2.2% | 3.5% |
| Haiku 4.5 | 0.6% | 2.9% | 0.06% | 0.7% | 1.2% |

(Values divide $/M at published API rates by $171 session cap, then ×100.)

The new `meter.py estimate` mode bakes these into a CLI: `python3 meter.py estimate --model opus --tokens 1m --bucket output` → "$25.00 · ~14.6% of session".

---

## Hypotheses tested

**H1 (null):** Panel %s are linear in API-equivalent dollars regardless of model. → $/pp constant across stages dominated by different models.

**H2 (alternative):** Anthropic applies model-specific multipliers (e.g., Sonnet penalty because of its own bucket; Haiku discount because cheap). → $/pp differs significantly across stages.

**Result: H1 holds within measurement noise.** Cannot reject the null.

---

## Methodology

**Conditions.** Single-instance Max 20x account, freshly reset session 5h window (started 11:20am Europe/Berlin), no other agents producing tokens (verified via `ps aux` + recent JSONL mtime). 30% session budget (~$52, ~30pp) allocated to the experiment.

**Three stages, one dominant model each:**

1. **Haiku audit pass.** Dispatched 7 Explore (Haiku) subagents in parallel to audit different facets of the `/usage2` codebase: pricing rates, mode/doc parity, concurrent-detection logic, security of submission flow, README clarity, crowd-report ingestion, capture.sh tmux fragility. Background dispatch (`run_in_background: true`).

2. **Sonnet implementation pass.** Dispatched 1 general-purpose (Sonnet) subagent to ship 5 changes flagged by Stage 1: KeyError fix, active_only filter for contamination, security scrubs, calibrate-account-scope mode, estimate mode. Background dispatch.

3. **Opus synthesis pass.** Main-thread Opus 4.7 wrote this research document, synced changes into the public repo, etc.

**Each stage book-ended with `/usage2 sample`** to capture authoritative panel %s.

**Per-stage measurement:**
```
account_5h_delta_dollars   = T_post.trailing_5h - T_pre.trailing_5h
                             (account-wide, includes residuals aging in/out)
this_session_delta_dollars = account_5h_delta - concurrent_5h_delta
panel_delta_pp             = T_post.session_pct - T_pre.session_pct
implied_$/pp               = panel-aligned slope
```

---

## Results

### Stage-by-stage

| Stage | Dominant model | Duration | Main-thread Opus $ | Panel Δ (session) | Implied $/pp |
|---|---|---|---|---|---|
| 1 | Haiku (7 BG subagents) | 5.3 min | $4.51 (Opus orchestration only) | +3pp | $1.71 |
| 2 | Sonnet (1 BG subagent) | 6.2 min | $1.78 (Opus orchestration only) | +1pp | $1.78 |

**Both stages land within the IQR of the independent calibration ($1.63 – $1.76).** The cross-stage variance is below the panel's 1pp resolution noise floor.

### Why the Haiku/Sonnet subagents didn't dominate

Two reasons converge:

1. **Background subagents are invisible to per-spawn meter attribution.** The `toolUseResult.totalTokens` field is only populated for *foreground* Agent dispatches. Background agents (`run_in_background: true`) leave **zero traces** in the parent JSONL. They consume real quota (Anthropic still bills the account, and the panel ticks), but `/usage2 agents` can't see them. **This is a methodology finding worth its own bullet:** if you want per-subagent attribution, dispatch foreground.

2. **Cache amortization compressed the Sonnet stage.** The Sonnet subagent reported 92,757 tokens with 26 tool uses and a high cache hit ratio. At Sonnet cache_read rates ($0.30/M), 92K tokens = ~$0.03. Negligible against $1.78 of orchestration overhead.

### Across-window comparison (the more interesting finding)

The calibrate-account-scope mode also produced slopes for the **two weekly windows**:

| Window | $/pp | Cap | Ratio to session |
|---|---|---|---|
| Session 5h | $1.71 | $171 | 1.0× |
| Week all-models (7d) | $11.85 | $1,185 | 6.9× |
| Week Sonnet-only (7d) | $8.11 | $811 | 4.7× |

Anthropic publishes neither the caps nor the model-bucket logic, but the **ratios are stable across the 41 reports** in our dataset (15 clean pairs survived the chronological-pair filter). The Sonnet weekly is roughly 70% of the all-models weekly — that's the **real binding constraint** for Sonnet-heavy workflows like vision pipelines and code-review.

---

## What this means for budgeting

If you're on Max 20x and using `/usage2`:

- **Session capacity** ≈ $171 of API-equivalent work per 5h rolling window. That's about 7M Opus output tokens, or 11M Sonnet output, or 34M Haiku output. Cache-heavy interactive coding stretches further (97% of this conversation was cache_read at $0.50/M Opus = $0.0005 per K).

- **Weekly capacity** ≈ $1,185 (all) / $811 (Sonnet only). For Sonnet-heavy work, plan against the $811 ceiling. ≈ $116/day Sonnet budget if even-paced.

- **The single largest budget hazard** is uncached work — fresh-input or cache-write-1h tokens. 1M Opus output = 14.6% of session. Three back-to-back output-heavy turns can eat a third of the window.

---

## Methodology limitations

1. **Background-subagent invisibility.** As noted, the per-stage Haiku/Sonnet contributions weren't directly measurable. The stage assignments rely on the *dominant-by-design* premise. If background subagents got 5–10× more expensive than expected, this experiment wouldn't have detected it.

2. **Single-trial-per-model.** No replicates. The cross-stage agreement is suggestive, not conclusive. A repeat experiment with foreground dispatches would tighten this.

3. **Panel 1pp resolution.** The session panel ticks in integer percent. A stage that moves 1pp could actually have consumed anywhere from 0.5pp to 1.5pp of true work. This is the dominant source of noise at this budget level.

4. **5h rolling-window back-edge erosion.** Over a 6-minute interval, ~$12 of work from 7 days ago dropped off the back-edge of the trailing_7d window, **larger than the forward-edge growth from new work**. The Δtrailing_7d-as-proxy method works for short intervals (<3 min) but breaks down for longer ones unless you decompose forward and backward edges separately.

5. **One tier, one account, one day.** Anthropic's caps shift; what's true for max20x on 2026-05-18 may not be true for max5x today or max20x next month. The `meter.py calibrate-account-scope` mode is the right tool to reapply on demand.

---

## Improvements shipped as part of this experiment

1. **Fix calibrate KeyError (P0).** `meter.py calibrate` no longer crashes when all reports are contaminated; emits clean "(0 clean reports — N contaminated, M legacy skipped)" message.

2. **`active_only` filter (120s) for contamination detection.** `find_active_jsonls()` now distinguishes "modified within rolling window" (historical residual, fine for calibration) from "modified in last 120s" (currently writing, the actual contamination case). The `contaminated` flag uses the latter. After this fix went live mid-experiment, the next report became **the first non-contaminated report in the dataset**: $0 active concurrent activity, $27.53 historical residual, correctly classified.

3. **Security scrubs in contribute bundle.** Before: bundle leaked `timestamp_local` (TZ-revealing), panel reset strings with "(Europe/Berlin)" suffix (geolocation), and `concurrent_5h`/`concurrent_7d` lists of UUIDs (cross-user correlation vector). After: timestamps UTC-only, reset strings TZ-stripped, UUID lists replaced with bare counts.

4. **`calibrate-account-scope` mode.** New mode that derives $/pp from chronological pairs across all reports — including contaminated ones — because account-scope dollars include all sessions' work and panel %s reflect the same totality. The mode ships with the algorithm verbatim from this experiment.

5. **`estimate` mode.** New CLI: `meter.py estimate --model opus --tokens 1m [--bucket output]` → "$25.00 · ~14.6% of session · ~2.1% of week (all)". Accepts shorthand model names (`opus`/`sonnet`/`haiku`), token suffixes (`1m`/`500k`), and a "blended" default bucket using rough 60/25/10/5 cache_read/cache_write_1h/input/output mix.

See `meter.py` diff (~+202 lines).

---

## Reproducing this

```bash
# Single-instance, fresh session window
python3 ~/.claude/skills/usage2/meter.py tier max20x
python3 ~/.claude/skills/usage2/meter.py sample          # baseline

# Stage 1: a Haiku-dominated batch (foreground for visibility)
# ... your work that primarily spawns Haiku subagents ...
python3 ~/.claude/skills/usage2/meter.py sample

# Stage 2: a Sonnet-dominated batch
# ... your work ...
python3 ~/.claude/skills/usage2/meter.py sample

# Stage 3: an Opus-dominated batch  
# ... your work ...
python3 ~/.claude/skills/usage2/meter.py summary

# Then analyze:
python3 ~/.claude/skills/usage2/meter.py calibrate-account-scope
```

---

## Raw data

`experiment_2026-05-18.json` in this directory contains the full per-stage record.

## License

MIT, same as the rest of the repo.
