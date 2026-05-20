# Max 20x token budgets — front-and-center reference

> **As of 2026-05-19 (v5).** Anthropic adjusts caps periodically; recalibrate when limits visibly shift. Full methodology: [`per_model_cost_v5.md`](per_model_cost_v5.md).

## TL;DR — how much can I do?

One **Max 20x 5-hour session window**, at 100% panel saturation, single-model strategy:

| Model      | Session cap ($-equivalent) | $/pp   |
|------------|----------------------------|--------|
| Haiku 4.5  | ~$44                       | $0.443 |
| Sonnet 4.6 | ~$46                       | $0.464 |
| Opus 4.7   | ~$50                       | $0.499 |

The panel is approximately model-neutral — $/pp differs by at most 12.6% across the three models.

**Weekly caps are not yet measured** — see the bottom section.

---

## The formulae

### Panel %s

Every Claude action consumes API-equivalent dollars at Anthropic's published rates. The `/usage` panel tracks cumulative dollars per rolling window against a per-tier cap:

```
session_pct(t)     = 100 × (Σ $_i across all calls in last 5h since reset) / session_cap
week_all_pct(t)    = 100 × (Σ $_i across all calls in last 7d since reset) / week_all_cap
week_sonnet_pct(t) = 100 × (Σ $_i across Sonnet-only calls in last 7d)     / week_sonnet_cap
```

For any single call on a given model:

```
$_call = (input_tokens          × R_in
        + output_tokens         × R_out
        + cache_read_tokens     × R_cr
        + cache_write_5m_tokens × R_cw5m
        + cache_write_1h_tokens × R_cw1h) / 1_000_000
```

API rates per million tokens:

| Model       | R_in  | R_out  | R_cr  | R_cw5m | R_cw1h |
|-------------|-------|--------|-------|--------|--------|
| Opus 4.7    | $5.00 | $25.00 | $0.50 | $6.25  | $10.00 |
| Sonnet 4.6  | $3.00 | $15.00 | $0.30 | $3.75  | $6.00  |
| Haiku 4.5   | $1.00 | $5.00  | $0.10 | $1.25  | $2.00  |

Anthropic's per-call stdout `total_cost_usd` is the authoritative cost figure. The meter's recomputation from the rate table above is used for cross-checks only.

### Per-tier caps (Max 20x, 2026-05-19)

| Window | Cap (USD API-equivalent) | Confidence |
|--------|--------------------------|------------|
| Session 5h — Haiku  | ~$44 | High — 11pp panel resolution, direct measurement |
| Session 5h — Sonnet | ~$46 | High — 13pp panel resolution, direct measurement |
| Session 5h — Opus   | ~$50 | Medium — 5pp panel resolution, sequential measurement |
| Week (all models)   | not measured | — |
| Week (Sonnet only)  | not measured | — |

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

The large cache_read figure is the ~62K-token Claude Code system prompt, read on every `claude -p` call, multiplied by the number of calls per pp.

### Projected to a 1:8 input:output workload

The measured workload above is output-skewed. For a balanced workload (1 input token per 8 output tokens), the session cap maps to the following token counts at published rates. Cost of "1 input + 8 output": Haiku $41/M, Sonnet $123/M, Opus $205/M.

**Method 1 — whole session cap spent on input+output at 1:8 (cache cost not counted):**

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

The two methods differ on whether the per-call ~62K-token system-prompt cache read is counted against the session budget.

### Pure single-bucket budgets

If a session's entire cap went to one token bucket of one model:

```
tokens(model, bucket) = (session_cap × 1_000_000) / R_bucket
```

(`R_bucket` is the published rate in $ per million tokens.)

| Model      | Pure input | Pure output | Pure cache_read | Pure cache_write_1h |
|------------|------------|-------------|-----------------|---------------------|
| Haiku 4.5  | 44M        | 8.8M        | 440M            | 22M                 |
| Sonnet 4.6 | 15.3M      | 3.07M       | 153M            | 7.67M               |
| Opus 4.7   | 10M        | 2M          | 100M            | 5M                  |

---

## Cache state

Cold (cache miss) vs hot (cache hit), per `claude -p` call:

| Model      | Cold $/call | Hot $/call | Cold ÷ Hot |
|------------|-------------|------------|------------|
| Haiku 4.5  | $0.096      | $0.024     | 4.0×       |
| Sonnet 4.6 | $0.212      | $0.080     | 2.7×       |
| Opus 4.7   | $0.540      | $0.238     | 2.3×       |

Per-call cache prefix (Claude Code system prompt + tool definitions): ~62K tokens. Claude Code uses 1-hour ephemeral caching; an entry survives ~1 hour after its last hit. Changing the user prompt invalidates ~25K of the ~62K cached tokens; ~37K survives a prompt change.

---

## Concurrency

Running `claude -p` calls in parallel inflates measured cost: concurrent processes each write the prompt cache before reading another's, producing redundant `cache_creation` tokens. Opus parallel=5 measured $0.639/pp; Opus sequential=1 measured $0.499/pp — a 22% difference. Run measurement calls sequentially.

---

## Weekly cap — not measured

The v5 experiment moved `week_sonnet` by only 2pp and `week_all` by 2pp during the Sonnet batch — too coarse to derive a weekly cap. A weekly-cap measurement needs a probe that moves the weekly window by ≥20pp, which is roughly 130 session-pp of activity spread across one calendar week.

---

## Reproducing

```bash
python3 ~/.claude/skills/usage2/meter.py sample
for i in $(seq 1 12); do
  claude -p --model claude-opus-4-7 --output-format json --dangerously-skip-permissions \
    "Write a vivid 2000-word literary short story." > /tmp/run_$i.json
done
python3 ~/.claude/skills/usage2/meter.py sample
# $/pp = sum(total_cost_usd) / (session_pct_after − session_pct_before)
```

Run calls sequentially, not in parallel. For other tiers (Pro $20/mo, Max 5x $100/mo) expect proportionally smaller caps.
