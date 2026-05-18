# Per-model cost vs. panel-% — v3 (Max 20x, 2026-05-18)

**Supersedes:** [`per_model_cost.md`](per_model_cost.md) (v1) and [`per_model_cost_v2.md`](per_model_cost_v2.md). Both prior versions failed methodologically. v3 finally isolates per-model effects.

**TL;DR:**
- **Max 20x session 5h cap ≈ $72** of API-equivalent work (range $66–$82). Far lower than v1 ($171) or v2 ($337–$730).
- **H1 (panel linear in API-$) holds within ~25%** across Haiku, Sonnet, Opus. Haiku and Sonnet match within 3% ($0.675/pp vs $0.656/pp). Opus runs ~22% higher ($0.823/pp) — small but real penalty, or panel-resolution noise.
- **The right protocol for per-model isolation is `claude -p --model X` subprocess** — not the Agent tool. The Agent tool taxes the orchestrator (foreground returns become parent cache_write_1h at parent rates; background dispatches are invisible to attribution but still consume quota). v1 and v2 both fell into this trap.

---

## Headline numbers (Max 20x, 2026-05-18, single-instance)

| Model | calls | API-$ (stdout) | tokens | Δsession | **$/pp** | tokens/pp |
|---|---|---|---|---|---|---|
| Haiku 4.5 | 240 | $6.08 | 13.6M | 9pp | **$0.675** | 1.51M |
| Sonnet 4.6 | 60 | $5.90 | 2.65M | 9pp | **$0.656** | 295K |
| Opus 4.7 | 20 | $9.06 | 1.34M | 11pp | **$0.823** | 122K |
| **Average** | — | — | — | — | **$0.72/pp** | — |

**Implied session 5h cap: ~$72** (3-model average × 100). Range: $66–$82.

**Cross-model $/pp spread: 22.6%.** Within H1's "panel linear in API-$" prediction window. Mild evidence of an Opus multiplier (~1.2×) but not strong enough to assert H2.

### Per-bucket per-pp (tokens consumed per 1pp of session, by model)

| Model | input/pp | output/pp | cache_read/pp | cache_write_1h/pp |
|---|---|---|---|---|
| Haiku | 268 | 82K | 1.34M | 93K |
| Sonnet | 805 | 24K | 210K | 60K |
| Opus | 11 | 7K | 13K | 102K |

(Variation reflects each model's natural cache behavior under repeated short-story prompts.)

---

## Methodology

### Protocol

For each model, batches of `claude -p --model <model_id> --output-format json --dangerously-skip-permissions "<prompt>" > /tmp/v3/<n>.json` run in parallel. Each subprocess is a separate `claude` process tree — it consumes quota and writes its own session JSONL, but **the parent never touches the response text**. Only the small metadata JSON (cost, token counts) is parsed by an aggregator script.

This sidesteps the "Agent-tool tax" (see below).

`/usage2 sample` taken before and after each batch. Panel %s and account-scope dollars recorded.

### Stages (strictly linear)

1. **Stage A — Haiku 4.5:** 4 batches of 30/60/90/60 calls (240 total). Prompt: "Write a 2000-word literary short story about <varied subject>." Same complexity per call.
2. **Stage B — Sonnet 4.6:** 2 batches of 30/30 (60 total). Same prompt class.
3. **Stage C — Opus 4.7:** 2 batches of 15/5 (20 total). Same prompt class.

### Why batch sizes shrink across stages

Opus tokens are ~5× more expensive than Haiku per million. To stay within the 33pp budget cap, Opus batches are sized down proportionally. End result: each stage moves the panel ~9–11pp.

### Why no Sonnet- or Opus-only weekly window movement

Week_all_pct moved 1pp during Opus stage, 2pp during Sonnet. Week_sonnet_pct moved 1pp during Sonnet stage only — confirming the Sonnet weekly is its own quota bucket. These deltas are at panel resolution, not enough to derive separate $/pp for weekly windows. The session 5h numbers above are the headline.

### Cost source: stdout `total_cost_usd`, not meter

The meter's JSONL-scanning `cost_of()` mis-attributes models with date-suffixed names (e.g., `claude-haiku-4-5-20251001` vs the RATES dict's `claude-haiku-4-5`) — it silently falls back to Sonnet pricing as DEFAULT_RATE_KEY. v3 uses Anthropic's authoritative `total_cost_usd` from each `claude -p` stdout, which reflects actual billing. **Meter bug noted; fix coming in a follow-up commit.**

---

## Hypothesis test

- **H1 (null):** Panel %s linear in API-equivalent dollars regardless of model. → $/pp constant across stages.
- **H2:** Per-model multipliers. → $/pp differs significantly.

**Result: H1 holds within 25%, supports it within 10% for Haiku vs Sonnet only.**

- Haiku $0.675/pp and Sonnet $0.656/pp differ by 3% — within panel-resolution noise.
- Opus $0.823/pp is 22% above Haiku/Sonnet average. This *could* be:
  - A real ~1.2× multiplier on Opus tokens (mild H2 evidence)
  - Panel resolution noise (Opus stage = 11pp ± 0.5pp = ±5% measurement noise alone; cache_creation tokens varied between batches contributing more noise)
  - Cache write pattern differences (Opus batches had higher cw1h/pp than Sonnet)

**Verdict:** Treat H1 as the working hypothesis. The meter's dollar-denominated calibration is approximately correct; if you're using Opus heavily, budget ~20% more cushion.

---

## Implied caps (Max 20x, 2026-05-18) — superseding all prior numbers

| Source | Session 5h cap |
|---|---|
| v1 — `account_scope_slopes()` over 41 contaminated reports | $171 (UNDER-estimate) |
| v2 — single-stage Opus orchestration | $337–$730 (OVER-estimate, methodology broken) |
| **v3 — three single-model `claude -p` stages** | **~$72** (range $66–$82) |

**Why v3 is more credible:**
- Direct API-billed `total_cost_usd` from each subprocess (no JSONL-scan double-counting)
- Each stage 100% one model by construction (no parent cost amplification)
- Three independent measurements bracket the answer with small spread
- Panel movement matches expected from API rates

**Why v1 underestimated:** The 41-report dataset included many short-interval pairs where the meter's flawed JSONL cost calculation (cache-write double-count, date-suffix model fallback) inflated the denominator. Account-scope slopes came out low.

**Why v2 overestimated:** Foreground subagent dispatch caused the parent's `cache_write_1h` to absorb subagent returns at Opus rates. The parent's API-billed cost was high, but the work was "really" the subagents' — most of the cost was caching their output. Without isolating that, the $/pp denominator overcounted.

---

## The "Agent-tool tax" — a real /usage2 limitation worth shipping as documentation

Every Agent dispatch (foreground OR background) writes the subagent's return into the parent's `cache_write_1h` on the next turn — at the parent's model rate (always Opus in interactive Claude Code sessions). This means:

- `/usage2 agents` mode shows per-spawn cost only for foreground dispatches, AND only for the subagent's own tokens. The parent-side cache_write amplification (often 20×+ the subagent's own cost) is hidden in the main-thread total.
- "Compare Haiku vs Sonnet on this task by dispatching one of each" is fundamentally untestable via the Agent tool.
- A/B comparisons that involve subagent dispatch are unreliable for per-model cost analysis.

**Recommendation for /usage2 users:** When doing per-model A/B testing, use `claude -p --model X` subprocesses rather than the Agent tool. The subprocess's output stays in its own JSONL; the parent never absorbs it.

### Concrete example (from v2):

```
Stage 1 v2 — 16 foreground Haiku Explore subagents:
  Haiku tokens billed (from toolUseResult):       $0.49
  Parent cache_write_1h absorbing returns:        $13.10  ← 27× amplification
  Apparent "Haiku stage" cost:                    $13.59 (98% Opus)
```

In v3 with `claude -p`:
```
Stage A — 240 Haiku claude -p calls:
  Total billed (stdout sum):                       $6.08
  Parent main-thread cost during stage:            ~$0 (only batch-orchestration turns)
  Apparent "Haiku stage" cost:                     $6.08 (100% Haiku)
```

---

## Per-1M-token reference (Max 20x, derived from v3 caps)

Using session cap ≈ $72:

| Model | 1M input | 1M output | 1M cache_read | 1M cache_write_1h |
|---|---|---|---|---|
| Opus 4.7 | 6.9% | **34.7%** | 0.69% | 13.9% |
| Sonnet 4.6 | 4.2% | 20.8% | 0.42% | 8.3% |
| Haiku 4.5 | 1.4% | 6.9% | 0.14% | 2.8% |

(Apply ×1.22 multiplier to Opus rows if you want to incorporate the mild observed Opus penalty.)

---

## Limitations

1. **Single tier, single day, single account.** Max 20x on 2026-05-18 only. Anthropic adjusts caps; recalibrate when limits visibly shift.
2. **Panel resolution still 1pp.** Each stage's $/pp has ~±5–10% noise from rounding.
3. **The 22% Opus discrepancy is suggestive, not conclusive.** A targeted retest with bigger Opus volume would tighten or refute it.
4. **Prompts kept short and similar.** Long-context prompts (where the subprocess loads heavy CLAUDE.md context) might show different ratios.

---

## Reproducing v3

```bash
# 1. Single instance, clean conditions
ps aux | grep claude | grep -v grep   # should show only the orchestrator
python3 ~/.claude/skills/usage2/meter.py sample   # baseline

# 2. Per model, batches of parallel claude -p calls
PROMPT='Write a vivid 2000-word literary short story about ...'
for i in $(seq 1 30); do
  claude -p --model claude-haiku-4-5 --output-format json --dangerously-skip-permissions "$PROMPT" \
    > /tmp/v3/haiku_$i.json &
done
wait

# 3. Aggregate cost from stdout JSONs (DO NOT trust meter's JSONL scan — date-suffix bug)
python3 -c "
import json, glob
total = 0
for f in glob.glob('/tmp/v3/haiku_*.json'):
  total += json.load(open(f))['total_cost_usd']
print(f'Haiku batch cost: \${total:.4f}')
"

# 4. Sample again
python3 ~/.claude/skills/usage2/meter.py sample

# 5. Compute Δsession, Δ$ → $/pp for that model
# 6. Repeat for sonnet (claude-sonnet-4-6), opus (claude-opus-4-7)
```

---

## Raw data

`experiment_2026-05-18_v3.json` — full per-stage record + per-call metadata.

## License

MIT.
