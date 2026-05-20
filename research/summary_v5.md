# Claude Code Max 20x — measured token economics

> Measured 2026-05-19 (Max 20x). Condensed findings. Full methodology: [`per_model_cost_v5.md`](per_model_cost_v5.md).

## Motivation

Anthropic does not publish per-tier session or weekly token caps for Claude Code subscribers. The `/usage` panel shows percentages without exposing the underlying tokens-per-percentage math.

## Method

Measured 315 `claude -p` subprocess calls (Haiku 4.5 × 205, Sonnet 4.6 × 75, Opus 4.7 × 35 parallel + 12 sequential, plus 4 prompt-cache tests) on Max 20x. Sampled the `/usage` panel via tmux before and after each batch. Took `total_cost_usd` and `usage.*` token buckets verbatim from each call's JSON stdout. Computed dollars and tokens per percentage point of the panel's 5-hour session window.

---

## Session 5-hour cap, single-model strategy

(Claude Max 20x, 100% panel saturation, measured on output-heavy workload, ~1:200 input:output)

| Model       | $-equivalent | output tokens | total tokens (incl. cache reads) |
|-------------|--------------|---------------|----------------------------------|
| Haiku 4.5   | $44          | 5.62M         | 121M                             |
| Sonnet 4.6  | $46          | 2.31M         | 25.8M                            |
| Opus 4.7    | $50          | 1.18M         | 16.6M                            |

Pairwise $-equivalent differences: Haiku vs Sonnet 4.8%, Haiku vs Opus 12.6%, Sonnet vs Opus 7.6%. All under 15%.

---

## Session 5-hour cap, projected to a 1:8 input:output workload

Derived by holding the measured cache overhead per pp constant (Claude Code system-prompt read on every call) and redistributing the remaining $/pp budget across input + output at a 1:8 ratio, using Anthropic's published per-bucket rates.

| Model       | input tokens at 100pp | output tokens at 100pp | total tokens (incl. cache) | $-equivalent |
|-------------|-----------------------|------------------------|----------------------------|--------------|
| Haiku 4.5   | 712K                  | 5.70M                  | 121.8M                     | $44          |
| Sonnet 4.6  | 284K                  | 2.27M                  | 25.8M                      | $46          |
| Opus 4.7    | 145K                  | 1.16M                  | 16.6M                      | $50          |

---

## Per `claude -p` call, cache state

| Model       | Cold (cache miss) | Hot (cache hit) | Ratio |
|-------------|-------------------|-----------------|-------|
| Haiku 4.5   | $0.096            | $0.024          | 4.0×  |
| Sonnet 4.6  | $0.212            | $0.080          | 2.7×  |
| Opus 4.7    | $0.540            | $0.238          | 2.3×  |

Per-call cache prefix (Claude Code system prompt + tool definitions): ~62K tokens. Read on every cache-hit invocation.

---

## Prompt-keyed cache behavior

4-call Haiku sequence; same prompt, then different prompt, then repeat:

| Call | Prompt | cache write (cw1h) | cache read |
|------|--------|--------------------|------------|
| 1    | A      | 62,322             | 0          |
| 2    | A      | 0                  | 62,322     |
| 3    | B      | 25,266             | 37,042     |
| 4    | B      | 0                  | 62,308     |

Changing the user prompt invalidates ~25K of the 62K cached tokens. ~37K remain cache-readable across user-prompt changes.

---

## Concurrency effect

Parallel = 5 simultaneous subprocesses vs sequential = 1 at a time:

| Model       | Parallel: calls with cw1h > 0 | Sequential: calls with cw1h > 0 |
|-------------|-------------------------------|---------------------------------|
| Haiku 4.5   | 5 of 200 (2.5%)               | not measured                    |
| Sonnet 4.6  | 5 of 70 (7.1%)                | not measured                    |
| Opus 4.7    | 10 of 30 (33%)                | 2 of 12 (17%)                   |

Opus parallel $/pp: $0.639. Opus sequential $/pp: $0.499. Difference: 22%.

---

## Repo

github.com/Loringtonian/usage2 — raw stdouts, scripts, and reproduction instructions.
