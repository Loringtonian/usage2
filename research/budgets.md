# Max 20x token budgets — front-and-center reference

> **As of 2026-05-18 (v4-refined).** Anthropic adjusts caps periodically; recalibrate when limits visibly shift. Derived from v3 + v4 experiments. Full methodology: [`per_model_cost_v4.md`](per_model_cost_v4.md).
>
> **What v4 added:** Opus's apparent 22% penalty was v3 noise (v4 deep-dive measured Opus at $0.545/pp). Cache state matters a lot (cold→hot 0.26–0.48× cost per model). Agent-tool tax characterized at ~11× ratio with an 80%-reduction workaround validated.

## TL;DR — how much can I do?

**Max 20x session 5h** ≈ **$72** of API-equivalent work. With 97% cache reuse, that's enough for ~half a day of interactive coding. Without caching, a few intense generation passes.

**Max 20x week** ≈ **$525–$800** all-models, **~$590** Sonnet-only (rough; needs v4 to tighten).

---

## The formulae

### Panel %s

Every Claude action consumes API-equivalent dollars at Anthropic's published rates. The /usage panel tracks cumulative dollars per rolling window against a per-tier cap:

```
session_pct(t)    = 100 × (Σ $_i across all calls in last 5h since reset)    / session_cap
week_all_pct(t)   = 100 × (Σ $_i across all calls in last 7d since reset)    / week_all_cap
week_sonnet_pct(t)= 100 × (Σ $_i across Sonnet-only calls in last 7d)        / week_sonnet_cap
```

Where for any single call on a given model:

```
$_call = (input_tokens × R_in
        + output_tokens × R_out
        + cache_read_tokens × R_cr
        + cache_write_5m_tokens × R_cw5m
        + cache_write_1h_tokens × R_cw1h) / 1_000_000
```

API rates per million tokens (verified 2026-05-17). **On Max 20x you only interact with the three current models** — older versions listed below for reference only.

**Current models (Max 20x default):**

| Model | R_in | R_out | R_cr | R_cw5m | R_cw1h |
|---|---|---|---|---|---|
| **Opus 4.7** | $5.00 | $25.00 | $0.50 | $6.25 | $10.00 |
| **Sonnet 4.6** | $3.00 | $15.00 | $0.30 | $3.75 | $6.00 |
| **Haiku 4.5** | $1.00 | $5.00 | $0.10 | $1.25 | $2.00 |

(Legacy: Opus 4.5/4.6 same rates as 4.7. Sonnet 4.5 same as 4.6. Opus 4.1 was 3× current Opus. Haiku 3.5 is retired except on Bedrock/Vertex.)

Multipliers: cache_write_5m = 1.25× input, cache_write_1h = 2× input, cache_read = 0.1× input.

### Per-tier caps (Max 20x, 2026-05-18)

| Window | Cap (USD API-equivalent) | Confidence |
|---|---|---|
| Session 5h | **$72** (range $66–$82) | High — 3 single-model stages, direct measurement |
| Week (all models) 7d | **~$700** (provisional $525–$800) | Low — only 4pp panel movement during v3 |
| Week (Sonnet only) 7d | **~$590** | Very low — only 1pp panel movement during v3 |

**No reproducible per-model multiplier.** v3 saw Opus 22% high; v4 saw Opus 17% low. Spread is panel-resolution noise (~25%). Treat all three models as ~$0.55–$0.72/pp for planning.

### Cache state matters

Cold vs hot cache changes per-call cost dramatically (v4 measurement):

| Model | Cold $/call (15-call batch) | Hot $/call (15-call batch) | Hot/cold |
|---|---|---|---|
| Haiku 4.5 | $0.067 | $0.032 | **0.48×** |
| Sonnet 4.6 | $0.160 | $0.072 | **0.45×** |
| Opus 4.7 | $0.518 | $0.133 | **0.26×** |

Plan Opus work in tight batches (within 5 minutes) to stack cache hits. Spreading Opus calls across hours gives up 3–4× cost.

### Solving for tokens-per-percent

Given the formulae above, to convert dollars → tokens for a specific (model, bucket) pair:

```
tokens_per_pp(model, bucket) = (session_cap × 1_000_000) / (100 × R_bucket)
                              = (session_cap × 10_000) / R_bucket
```

E.g., for Haiku output at session_cap = $72:
```
tokens_per_pp = (72 × 10_000) / 5.00 = 144_000 Haiku output tokens per 1pp session
              → 14.4M output tokens for a full session window
```

---

## Token budgets per session (Max 20x, session_cap ≈ $72)

100% of one 5-hour session window, in pure tokens of one bucket per model:

| Model | Pure input | Pure output | Pure cache_read | Pure cache_write_1h |
|---|---|---|---|---|
| **Opus 4.7** | 14.4M tokens | 2.88M tokens | 144M tokens | 7.2M tokens |
| **Sonnet 4.6** | 24M tokens | 4.8M tokens | 240M tokens | 12M tokens |
| **Haiku 4.5** | 72M tokens | 14.4M tokens | 720M tokens | 36M tokens |

Apply the empirical ~1.22× Opus penalty if you want a defensive estimate:

| Model | Pure input | Pure output | Pure cache_read | Pure cache_write_1h |
|---|---|---|---|---|
| **Opus 4.7** (with 1.22× penalty) | 11.8M | 2.36M | 118M | 5.9M |

### A typical Claude Code turn isn't pure-anything

Real interactive use is a blend dominated by cache_read (high re-use of system prompt + context). Typical Max 20x interactive coding session burns ~$0.50–$2 per minute, mostly via cache_read at $0.50/M Opus. **Cache hit ratio ≥ 95% stretches the $72 budget into 2–4 hours of coding.**

A weighted-blended estimate (60% cache_read + 25% cache_write_1h + 10% input + 5% output) per 1pp:

| Model | Tokens/pp (blended) | Cost/pp (blended) |
|---|---|---|
| Opus 4.7 | ~1.5M tokens | ~$0.96/pp (or $0.72/pp at cap=72) |
| Sonnet 4.6 | ~2.5M tokens | ~$0.55/pp |
| Haiku 4.5 | ~7.4M tokens | ~$0.18/pp |

(These are derived from the formula above, not from v3's empirically observed mix.)

---

## Token budgets per week (Max 20x, week_all_cap ≈ $700 provisional)

| Model | Pure input/wk | Pure output/wk | Pure cache_read/wk |
|---|---|---|---|
| Opus 4.7 | 140M | 28M | 1.4B |
| Sonnet 4.6 | 233M | 47M | 2.33B |
| Haiku 4.5 | 700M | 140M | 7B |

**Sonnet weekly is the binding constraint** (~$590 cap, lower than all-models on the same dollar basis). At 100% Sonnet, you get:

- 197M Sonnet input · 39.3M Sonnet output · 1.97B Sonnet cache_read per week

---

## Practical planning tables

### "How many of X can I do?"

Within ONE session 5h window (Max 20x):

| Activity | Approx count |
|---|---|
| Full interactive coding hours (97% cache) | 2–4 hours |
| 2000-word Haiku short stories | ~240 |
| 2000-word Sonnet short stories | ~60 |
| 2000-word Opus short stories | ~15–20 |
| Average Claude Code turn (cache-warm Opus, ~5K cache_read + 5K output) | ~280 turns |
| Heavy Opus output turns (40K cache_read + 30K output) | ~70 turns |

### "How much does X cost as a % of session?"

Run: `python3 meter.py estimate --model <m> --tokens <n> --bucket <b>` for arbitrary cases.

---

## Reverse calculation: given panel %, how much $ have I spent?

```
$_spent_in_session = (session_pct / 100) × $72
```

So at session_pct = 50%, you've consumed about $36 of API-equivalent work in the last 5 hours. If you wanted to check this against `/usage2 quick`, the meter's `trailing_5h.dollars` should be within ~10–20% of this calculation under typical interactive use.

---

## Caveats / what's not tight yet

- **Weekly numbers are noisy.** v3 burned ~$21 of measured Sonnet/Haiku/Opus during single-model isolation; the all-models weekly only moved 4pp and Sonnet-only weekly moved 1pp. Need v4 to tighten.
- **Opus surcharge unconfirmed.** Could be a real ~1.22× weighting or panel-resolution noise. v4 with more Opus volume will resolve.
- **Date-suffixed model names in JSONL** previously caused the meter to mis-attribute Haiku as Sonnet pricing. **Fixed 2026-05-18.** See `meter.py:normalize_model_name()`.
- **Cache_creation tokens are charged at cache_write_5m or cache_write_1h depending on TTL hint.** Anthropic's stdout `total_cost_usd` is the source of truth — the meter's recomputation can drift if rates change.
- **Anthropic adjusts caps periodically.** Recalibrate with `meter.py calibrate-account-scope` when limits visibly shift.

---

## Reproducing

```bash
# Verify your account's current cap
for model in claude-haiku-4-5 claude-sonnet-4-6 claude-opus-4-7; do
  python3 ~/.claude/skills/usage2/meter.py sample
  for i in $(seq 1 30); do
    claude -p --model $model --output-format json --dangerously-skip-permissions \
      "Write a vivid 2000-word short story." > /tmp/v_$model_$i.json &
  done
  wait
  python3 ~/.claude/skills/usage2/meter.py sample
  # Sum total_cost_usd; divide by panel session_pct delta
done
```

For other tiers (Pro = $20/mo, Max 5x = $100/mo), expect proportionally smaller caps. Pro session 5h ≈ $7, Max 5x ≈ $36 (linear scaling assumed; not yet empirically verified for those tiers).
