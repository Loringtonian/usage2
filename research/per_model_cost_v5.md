# Per-model cost — v5 (Max 20x, 2026-05-19)

**Supersedes:** [v1](per_model_cost.md), [v2](per_model_cost_v2.md), [v3](per_model_cost_v3.md), [v4](per_model_cost_v4.md).

v5 measures per-model session cost at ≥10 percentage-points of panel resolution per model (v3/v4 had 1–4pp), isolates a parallelism-induced measurement artifact that inflated v4's Opus number, and maps the session cap to token counts.

## TL;DR

1. **Session 5-hour cap, single-model strategy (Max 20x):** Haiku ~$44, Sonnet ~$46, Opus ~$50 of API-equivalent value at 100% panel saturation.
2. **The panel is approximately model-neutral.** $/pp: Haiku $0.443, Sonnet $0.464, Opus $0.499. Maximum pairwise difference 12.6%.
3. **v4's "Opus is cheaper" / v3's "Opus penalty" were both measurement artifacts.** Running `claude -p` calls in parallel inflates Opus cost by 22% via redundant cache writes (parallel processes race to create the prompt cache before reading each other's). Sequential measurement removes it.
4. **Changing the user prompt partially invalidates the cache.** ~25K of the ~62K-token cached prefix is rewritten; ~37K (the Claude Code system prompt + tool definitions) survives a prompt change.

---

## Method

315 `claude -p --output-format json --dangerously-skip-permissions` subprocess calls on a Max 20x account:

| Batch | Model | Calls | Concurrency |
|-------|-------|-------|-------------|
| v5-ext Haiku  | Haiku 4.5  | 205 | parallel = 5 |
| v5-ext Sonnet | Sonnet 4.6 | 75  | parallel = 5 |
| v5-ext Opus   | Opus 4.7   | 35  | parallel = 5 |
| Follow-up A   | Opus 4.7   | 12  | sequential = 1 |
| Follow-up B   | Haiku 4.5  | 4   | sequential = 1 |

The `/usage` panel was sampled (tmux scrape) before and after each model's batch. Each call's `total_cost_usd` and `usage.*` token buckets were taken verbatim from its JSON stdout. `total_cost_usd` from Anthropic stdout is the authoritative cost figure; the meter's own rate-table recomputation is used only for cross-checks.

Per-model batches ran sequentially with a 6-minute gap between models. Each model batch moved the panel ≥10pp (the v5-extended run) or ≥5pp (the sequential Opus follow-up).

Prompt: a 2000-word literary short story, unique topic per model. Input is ~6–10 tokens per call; output is 2,500–8,100 tokens per call. This is a heavily output-skewed workload; see the 1:8 projection below for a balanced-workload estimate.

---

## Session 5-hour cap by model

`$/pp` = Anthropic-stdout dollars divided by panel session-percentage-points moved. Session cap = `$/pp × 100`.

| Model      | Δsession | Anthropic $ | $/pp   | Session cap (100pp) |
|------------|----------|-------------|--------|---------------------|
| Haiku 4.5  | 11pp     | $4.872      | $0.443 | **$44**             |
| Sonnet 4.6 | 13pp     | $6.032      | $0.464 | **$46**             |
| Opus 4.7   | 5pp      | $2.492      | $0.499 | **$50**             |

Opus figure is the sequential measurement. Pairwise $/pp differences: Haiku vs Sonnet 4.8%, Haiku vs Opus 12.6%, Sonnet vs Opus 7.6%.

---

## Session cap in tokens

### As measured (output-heavy workload, ~1:200 input:output)

100% of one 5-hour session window, single-model strategy:

| Model      | input tokens | output tokens | cache_read tokens | cw1h tokens | total tokens |
|------------|--------------|---------------|-------------------|-------------|--------------|
| Haiku 4.5  | 19K          | 5.62M         | 112.3M            | 3.09M       | 121M         |
| Sonnet 4.6 | 1.7K         | 2.31M         | 21.9M             | 1.30M       | 25.8M        |
| Opus 4.7   | 1.4K         | 1.18M         | 13.2M             | 2.17M       | 16.6M        |

### Per panel-percentage-point, by token bucket

| Model      | input/pp | output/pp | cache_read/pp | cw1h/pp |
|------------|----------|-----------|---------------|---------|
| Haiku 4.5  | 186      | 56,190    | 1,122,644     | 30,935  |
| Sonnet 4.6 | 17       | 23,067    | 219,383       | 13,015  |
| Opus 4.7   | 14       | 11,817    | 131,527       | 21,724  |

cache_read per pp is large because every `claude -p` call reads the ~62K-token Claude Code system prompt from cache; the per-pp figure is that prefix times the number of calls per pp.

### Projected to a 1:8 input:output workload

The measured workload is output-skewed. For a balanced workload (1 input token per 8 output tokens), the session cap maps to the following token counts at Anthropic's published rates.

Cost of 1 input + 8 output tokens: Haiku $41/M, Sonnet $123/M, Opus $205/M (units of "1in+8out").

**Method 1 — entire session cap spent on input+output at 1:8 (cache cost not counted):**

| Model      | session cap | input tokens | output tokens |
|------------|-------------|--------------|---------------|
| Haiku 4.5  | $44         | 1.073M       | 8.585M        |
| Sonnet 4.6 | $46         | 0.374M       | 2.992M        |
| Opus 4.7   | $50         | 0.244M       | 1.951M        |

**Method 2 — measured cache overhead held aside, remainder spent on input+output at 1:8:**

| Model      | session cap | cache reads+writes | input tokens | output tokens |
|------------|-------------|--------------------|--------------|---------------|
| Haiku 4.5  | $44         | ~$15               | 0.705M       | 5.639M        |
| Sonnet 4.6 | $46         | ~$12               | 0.280M       | 2.244M        |
| Opus 4.7   | $50         | ~$20               | 0.145M       | 1.163M        |

The two methods differ on whether the per-call ~62K-token Claude Code system-prompt cache read is counted against the session budget. Method 2's cache figure is the value measured on this experiment's workload; a workload with shorter per-call outputs runs more calls per pp and therefore carries higher cache overhead.

---

## Cache state — cold vs hot per call

| Model      | Cold (cache miss) | Hot (cache hit) | Cold ÷ Hot |
|------------|-------------------|-----------------|------------|
| Haiku 4.5  | $0.096            | $0.024          | 4.0×       |
| Sonnet 4.6 | $0.212            | $0.080          | 2.7×       |
| Opus 4.7   | $0.540            | $0.238          | 2.3×       |

Per-call cache prefix (Claude Code system prompt + tool definitions): ~62K tokens. A cold call writes that prefix to cache; a hot call reads it.

Claude Code uses 1-hour ephemeral caching (`cache_creation.ephemeral_1h_input_tokens` is populated; `ephemeral_5m_input_tokens` is 0). A cache entry survives ~1 hour after its last hit.

---

## Prompt-keyed cache behavior

Four sequential Haiku calls — same prompt twice, then a different prompt twice:

| Call | Prompt | cache write (cw1h) | cache read | $ |
|------|--------|--------------------|------------|---|
| 1 | A (literary story)  | 62,322 | 0      | $0.0929 |
| 2 | A (literary story)  | 0      | 62,322 | $0.0202 |
| 3 | B (math problem)    | 25,266 | 37,042 | $0.0402 |
| 4 | B (math problem)    | 0      | 62,308 | $0.0116 |

Changing the user prompt invalidates ~25K of the ~62K cached tokens and rewrites them. ~37K (the invariant Claude Code system prompt + tool definitions) remains cache-readable across the prompt change.

---

## Concurrency artifact

Running `claude -p` calls concurrently inflates measured cost. When N processes start in parallel, each begins writing the prompt cache before it can read another's — producing redundant `cache_creation` (cw1h) tokens.

| Model      | Parallel = 5: calls with cw1h > 0 | Sequential = 1: calls with cw1h > 0 |
|------------|-----------------------------------|-------------------------------------|
| Haiku 4.5  | 5 of 200 (2.5%)                   | not measured                        |
| Sonnet 4.6 | 5 of 70 (7.1%)                    | not measured                        |
| Opus 4.7   | 10 of 30 (33%)                    | 2 of 12 (17%)                       |

Opus $/pp: parallel = 5 → $0.639; sequential = 1 → $0.499. The 22% difference is the parallel-race tax. Opus shows the largest effect because Opus calls are slow, so a larger fraction of any batch overlaps with the cache-creation window.

The v5 session caps above use the sequential Opus number. The parallel Opus batch is retained in the raw data as `opus_4_7_parallel` and is not used for the headline cap.

---

## Hypothesis outcomes

Two hypotheses were pre-registered:

- **HM1 (model-neutral):** the panel charges the same $/pp regardless of model, within ±15%. **Supported** — maximum pairwise difference 12.6%.
- **HM2 (Opus penalty ≥15%):** Opus consumes panel-pp at a materially higher $/pp. **Not supported** — Opus is +9.9% above the Haiku/Sonnet average, under the 15% threshold. The larger Opus figure seen in v3 and v4 was measurement noise and parallel-race tax.

---

## Weekly cap

Not measured. The Sonnet batch moved `week_sonnet` by 2pp and `week_all` by 2pp — too coarse to derive a defensible weekly cap. A weekly-cap measurement needs a probe that moves the weekly window by ≥20pp, which is ~130 session-pp of activity spread across one calendar week.

---

## Raw data

[`experiment_2026-05-19_v5.json`](experiment_2026-05-19_v5.json) — per-model token-bucket sums, panel samples, Experiment A (sequential Opus) and Experiment B (prompt-cache test).

---

## Reproducing

```bash
# Sample the panel, run a single-model batch, sample again.
python3 ~/.claude/skills/usage2/meter.py sample
for i in $(seq 1 12); do
  claude -p --model claude-opus-4-7 --output-format json --dangerously-skip-permissions \
    "Write a vivid 2000-word literary short story." > /tmp/run_$i.json
done
python3 ~/.claude/skills/usage2/meter.py sample
# $/pp = sum(total_cost_usd) / (session_pct_after - session_pct_before)
```

Run calls sequentially, not in parallel — parallel runs inflate cost via redundant cache writes (see Concurrency artifact). Recalibrate when Anthropic adjusts caps.
