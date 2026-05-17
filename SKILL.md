---
name: usage2
description: Give the agent visibility into its own token consumption. Reads Claude Code's per-message `usage` blocks straight from the session transcript JSONL — authoritative input/output/cache token counts for the main thread AND every subagent (Task) dispatch — and supports `mark`/`since` checkpoints for A/B comparisons ("did this approach burn fewer tokens than that one?"). Falls back to the built-in `/usage` panel (via a tmux scrape) for rolling-window quota state. Use when the user says "/usage2", "how many tokens", "token cost of X", "compare token usage", "am I being efficient", "what's my quota", "how close to the limit", "subagent cost", "which subagent burned the most", or whenever the agent needs to reason about its own token efficiency or session budget.
allowed-tools: Bash
---

# /usage2

Two capabilities in one skill:

1. **Token meter** (fast, ~10ms) — reads the local session transcript JSONL and reports authoritative per-action token consumption: main-thread turns, subagent dispatches, cache hit ratios, A/B deltas.
2. **Quota panel** (slow, ~12s) — captures Claude Code's built-in `/usage` panel via tmux to surface the rolling 5h / 7d / Sonnet-only meters with reset times.

## When to use which

| Question                                            | Mode                         | Cost  |
|-----------------------------------------------------|------------------------------|-------|
| How many tokens has this session burned?            | `summary` or `quick`         | ~10ms |
| Which subagent dispatch cost the most?              | `agents`                     | ~10ms |
| Did approach A cost more tokens than approach B?    | `mark` → do work → `since`   | ~10ms |
| How close to my 5-hour / weekly limit am I?         | `quota`                      | ~12s  |
| Snapshot for a downstream script                    | `raw` (JSON output)          | ~10ms |

Default to the cheap modes. Reserve `quota` for when the user explicitly asks about rolling-window limits or when the agent is gating an expensive autonomous decision on remaining quota.

## Invocation

```bash
python3 ${CLAUDE_SKILL_DIR}/meter.py [mode] [args]
```

Modes:

- `summary` — (default) full breakdown: main thread, subagents, efficiency signals
- `quick` — one-line totals (ideal for polling in a `/loop`)
- `agents` — per-subagent attribution only
- `mark <name>` — save a checkpoint at the current JSONL byte offset
- `since <name>` — report delta since a saved checkpoint
- `marks` — list saved checkpoints
- `drop <name>` — delete a checkpoint
- `raw` — dump aggregated counters as JSON
- `quota` — falls through to the tmux scrape of the built-in `/usage` panel

For `quota` specifically, run the legacy capture script directly:

```bash
bash ${CLAUDE_SKILL_DIR}/capture.sh
```

## A/B comparison workflow

The core experimentation pattern. Use this to settle questions like *"does sending the image at native resolution cost more tokens than resizing to 1024×1024?"*:

```bash
# 1. Mark the start
python3 ${CLAUDE_SKILL_DIR}/meter.py mark approach-A

# 2. Do approach A (one or several tool calls / subagent spawns)
#    ... agent does the work ...

# 3. Measure the delta
python3 ${CLAUDE_SKILL_DIR}/meter.py since approach-A
#    → reports tokens consumed since the mark, with main/subagent split

# 4. Mark again before approach B
python3 ${CLAUDE_SKILL_DIR}/meter.py mark approach-B

# 5. Do approach B
#    ... agent does the work ...

# 6. Measure
python3 ${CLAUDE_SKILL_DIR}/meter.py since approach-B
```

The agent then compares the two deltas and reports which approach is cheaper, by what margin, and where the difference comes from (input? output? cache reuse?).

## Output: what the agent should look for

Default `summary` mode prints sections for: **main thread**, **subagents** (with `agentType` grouping and prompt previews), **grand total**, **efficiency signals**.

Key signals:

- **Cache hit ratio** — `cache_read / (cache_read + cache_write + fresh_input)`. Above 80% is healthy context reuse; below 50% means the context is churning (something is invalidating the cache, or each turn loads fresh data).
- **Output / fresh-input ratio** — how verbose the agent is per new token of input. Higher means the agent is generating more per unit of new instruction.
- **Per-subagent breakdown** — which subagent dispatches dominated the spend, with their prompts visible. Use this to spot "I keep spawning Explore for things that didn't need it" patterns.

## Autonomous self-throttling

Pair the meter with a polling loop for long autonomous work. Example instruction the user gives the agent:

> Every 10 minutes, run `/usage2 quick`. If `cache hit` drops below 60% or grand-total grows by more than 500K tokens since the last check, pause and report. If you need to know how close I am to my hard limit, run `/usage2 quota`.

Cheap because `quick` is ~10ms and doesn't spawn a process.

## Caveats

- **The current in-flight turn is not yet in the JSONL** — Claude Code writes the assistant message + usage block only after the turn completes. So the meter is always one turn behind the live state. Fine for between-turn polling; not suitable for in-turn metering.
- **Subagents are aggregated, not per-step** — `toolUseResult.totalTokens` gives the full cost of a subagent dispatch, but you can't see its internal turn-by-turn breakdown from the parent transcript. (Subagents may have their own JSONL files in `~/.claude/projects/`; the meter doesn't currently descend into those.)
- **Hooks aren't included** — if a PostToolUse / PreCompact hook injects context, that shows up in the next assistant turn's input count, but isn't separately attributed.
- **"Current session" = most-recently-modified JSONL** in the cwd's project slug dir. If multiple Claude Code instances are running in the same project, the meter reads whichever was written to most recently.

## Failure modes

- `ERR: no JSONL found for project slug '...'` — the cwd doesn't have a Claude Code transcript yet (brand new project) or the project-slug rule has changed (CC version drift). Check `~/.claude/projects/` manually.
- `ERR: no mark named 'X'` — `since X` was called without a prior `mark X`.
- `quota` mode failures — see `capture.sh` failure modes (claude UI didn't render, usage panel didn't show). Re-run.
