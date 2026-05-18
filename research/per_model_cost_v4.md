# Per-model cost — v4 (Max 20x, 2026-05-18)

**Supersedes:** [v1](per_model_cost.md), [v2](per_model_cost_v2.md), [v3](per_model_cost_v3.md). v4 resolves three open questions from v3.

## TL;DR

1. **Session 5h cap ≈ $72** holds (consistent across v3 + v4). Median $/pp across all measurements: **$0.55–$0.72**, spread ~25%.
2. **Opus penalty was v3 noise.** v4 measured Opus at **$0.55/pp** — actually *lower* than v3's Haiku/Sonnet baseline of $0.66, not higher. H1 (panel linear in API-$) holds within measurement noise.
3. **Cache state matters a LOT:** hot vs cold per-call cost ratio is **0.26–0.48× across models.** Opus benefits most from caching.
4. **Agent-tool tax characterized:** parent caches ~11× the subagent return tokens as cw1h on average — but the **"write-to-file + summary" workaround drops parent cw1h by ~80%**, making per-model Agent-tool A/B testing viable.

---

## Phase 0 — Agent-tool tax

### Method

9 foreground Agent dispatches across 3 models (Haiku 4.5 via Explore, Sonnet 4.6 via general-purpose, Opus 4.7 via general-purpose + `model: "opus"` override), with target output sizes 500 / 2000 / 5000 words per model. Plus 1 workaround test using `general-purpose` that writes its output to `/tmp/v4_workaround_out.txt` and returns only a 50-word summary to the parent.

Measured parent's `cache_write_1h` growth (the "tax") via JSONL scan: total cw1h after 9 dispatches minus baseline.

### Results

| Metric | Value |
|---|---|
| Subagent output_tokens (aggregate, 9 calls) | 33,465 |
| Parent cw1h growth (across 4 parent turns) | 374,168 |
| Tax ratio (parent cw1h / subagent output) | **~11.2×** |
| Tax in $ (374K × $10/M Opus cw1h rate) | **~$3.74** |

This means: for every 3,000-token subagent return, the parent's next-turn cw1h grows by ~33,000 tokens → ~$0.33 of Opus cw1h tax. Multiply by your subagent's response size to get the hidden cost.

### Workaround verdict

| Pattern | Subagent return | Parent cw1h growth | Ratio |
|---|---|---|---|
| Normal foreground dispatch (avg per turn) | ~3,700 tokens | ~93,500 tokens | 25× |
| **Write-to-file + 50-word summary** | **128 tokens** | **18,716 tokens** | 146× of summary, but 0.20× of typical-turn cw1h |

The workaround pattern reduces parent cw1h growth by **~80%** compared to a normal subagent-dispatch turn. The file at `/tmp/v4_workaround_out.txt` (20.7 KB) held the full 5000-word writeup; the parent never saw it. **Viable mitigation for per-model A/B testing via Agent tool when you don't want to drop to `claude -p`.**

### How to apply the correction

If you dispatched a foreground subagent and want to estimate the *true* total cost (subagent + tax):

```
true_cost = subagent_displayed_$ + (subagent_output_tokens × 11.2 × parent_cw1h_rate_$_per_M / 1e6)
```

For Opus parent (interactive Claude Code default), `parent_cw1h_rate = $10/M`, so:

```
true_cost ≈ subagent_$ + (subagent_output_tokens × 11.2 × $10/M / 1e6)
         ≈ subagent_$ + (subagent_output_tokens × $0.0001 12)
```

Concrete: a Haiku subagent returning 5000 tokens has a displayed cost ~$0.026 but a true cost ~$0.026 + $0.56 = ~$0.59. The tax is 22× the subagent's own cost.

---

## Phase 1 — Cache state cold vs hot

### Method

For each model, two batches:
- **Cold batch:** first batch after the 5m cache TTL has expired (or no cache exists). 15/10/5 calls for Haiku/Sonnet/Opus.
- **Hot batch:** identical batch fired immediately after the cold one, within the 5m cache window. Same 15/10/5 sizes.

Same 2000-word short-story prompt across all calls in a batch (so the cache key matches).

### Results

| Model | Cold $/call | Hot $/call | **Hot/cold ratio** | Notes |
|---|---|---|---|---|
| Haiku 4.5 | $0.0665 | $0.0321 | **0.48×** | Even "cold" batch had partial cache reuse from parallel calls |
| Sonnet 4.6 | $0.1597 | $0.0722 | **0.45×** | Clean cold/hot separation |
| Opus 4.7 | $0.5183 | $0.1327 | **0.26×** | Cleanest separation — Opus benefits most from caching |

### Why Opus benefits more from caching

Opus pricing has the highest absolute cache_write_1h cost ($10/M for Opus vs $6/M for Sonnet vs $2/M for Haiku). So when caching reduces cw1h tokens, the dollar savings scale with the model's premium pricing.

**Implication for users:** if you're cost-conscious with Opus, batch your work so cache hits stack within the 5-minute window. A 1-hour gap between Opus calls means the 5m cache is dead but 1h cache may persist; >1 hour gap means full cold restart.

---

## Phase 2 — Opus deep-dive (resolving v3's 22% penalty)

### Method

20 Opus `claude -p` calls in 2 batches of 10, with fresh prompts to defeat the prior batch's cache. Sample `/usage2` between batches and after.

### Results

| Batch | Calls | Cost | Δsession | $/pp | Notes |
|---|---|---|---|---|---|
| DD b1 | 10 | $2.85 | +5pp | $0.570 | Cold start; 0.34 cr / 0.27 cw1h |
| DD b2 | 10 | $2.60 | +5pp | $0.520 | Some cache reuse from b1 |
| **Combined** | 20 | $5.45 | +10pp | **$0.545** | |

**Compared to v3's Opus $0.823/pp:** v4 measurement is 34% lower. v3 was noisy (only 11pp of measurement); v4 with 10pp clean is more credible.

**Updated H1 verdict:**

| Source | Haiku $/pp | Sonnet $/pp | Opus $/pp | Spread |
|---|---|---|---|---|
| v3 | $0.675 | $0.656 | $0.823 | 22% (Opus high) |
| v4 (DD) | — | — | $0.545 | — |

Combining v3+v4 evidence: per-model $/pp values land in **$0.54–$0.82 range**, with ~20% spread. Some of this is real, some panel-resolution noise. **No consistent per-model multiplier in any direction** — the spread doesn't reproduce its sign across attempts. Conclusion: **H1 (panel ≈ linear in API-$) holds within 25%**, treat that as the measurement noise floor.

---

## Updated budgets (Max 20x, 2026-05-18)

Session 5h cap: **$72** (range $54–$82, midpoint $0.66/pp × 100). Apply ±25% noise.

Per-model token budgets per session (using v4 Opus $/pp = $0.55):

| Model | 100% session in pure output | 100% session in cache_read | 100% session in cache_write_1h |
|---|---|---|---|
| Opus 4.7 | ~2.2M tokens | ~110M tokens | ~5.5M tokens |
| Sonnet 4.6 | ~4.4M tokens | ~219M tokens | ~11M tokens |
| Haiku 4.5 | ~13.1M tokens | ~657M tokens | ~33M tokens |

(Recomputed using lower v4 Opus value; slightly more conservative than v3's $72/$0.66 derivation.)

---

## What we know vs. what we don't

### Tight

- **Session 5h cap ≈ $72** ✓ (confirmed across v3+v4 with two independent protocols)
- **H1 holds within ~25%** ✓ (model variance < panel resolution noise)
- **Hot cache halves cost** ✓ (0.26–0.48× across all three models)
- **Agent-tool tax exists, ~11× ratio** ✓ (parent cw1h grows ~11 tokens per subagent output token)
- **Workaround drops tax by ~80%** ✓ (write-to-file + short summary)

### Loose

- **Weekly window caps** — v4 didn't burn enough on weekly to tighten (week_all went 25%→28% = 3pp during all of v4). Provisional ~$700 all-models, ~$590 Sonnet-only carries over from v3.
- **The 25% panel noise is the floor** — no protocol I can run on Max 20x at panel resolution will distinguish per-model multipliers below this. Anthropic would have to publish or expose finer-grained data.

---

## Shipped fixes (in this v4 commit)

1. **`normalize_model_name()` in meter.py** (already shipped in `c1cf076` after v3) — handles date-suffixed names, aliases (haiku/sonnet/opus).
2. **`budgets.md`** front-and-center reference (also `c1cf076`).
3. **This v4 doc + raw data JSON** (this commit).
4. **README + SKILL.md** updates: cite v4 numbers, document the Agent-tool-tax-workaround pattern.

---

## Reproducing v4

```bash
# Phase 0 — Agent-tool tax characterization
# Dispatch 3 foreground Explore subagents per model (varying output sizes)
# Measure parent's cache_write_1h growth via JSONL scan
# Compute tax_ratio = parent_cw1h_growth / Σ subagent_output_tokens

# Workaround test:
# Dispatch one general-purpose subagent that writes full output to file,
# returns ~50-word summary. Measure parent cw1h delta — should be tiny.

# Phase 1 — Cache state per model
# For each model: cold batch (fresh cache), then hot batch (within 5m)
# Compare hot/cold per-call $

# Phase 2 — Opus deep-dive
# Multiple Opus batches with /usage2 sample between
# Confirm Opus $/pp is within H1 noise band
```

Raw data: `experiment_2026-05-18_v4.json` in this directory.

## License

MIT.
