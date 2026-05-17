# usage2

A Claude Code skill that gives the agent **visibility into its own token consumption** — main thread, per-subagent, with cache breakdown and A/B checkpoints — so it can reason about its own efficiency instead of flying blind.

```
## Token usage
### Main thread (200 turns, 117 tool calls)
  Input (fresh):      121.9K
  Output:             265.0K
  Cache read:         24.50M  (94% of all input)
  Cache write:        1.46M
  Main-thread total:  26.35M

### Subagents (3 spawns, 187.9K tokens)
  Explore × 3: 187.9K
    └─ 52.0K  in 6  out 1.9K  cache-r 49.7K  45.4s  16 tools  "Audit branch ship-readiness…"
    └─ 61.9K  in 4  out 1.9K  cache-r 59.9K  66.1s  30 tools  "Plan a fix for two broken hooks…"
    └─ 74.0K  in 2  out 3.4K  cache-r 70.4K  83.2s  21 tools  "Plan a Buckminster Fuller deep dive…"

### Grand total: 26.54M tokens
### Efficiency signals
  Cache hit ratio:       94%   (good context reuse)
  Output / fresh input:  0.17x
```

## Why this exists

Until now, when you ask Claude "am I being token-efficient?" or "did approach A cost more than approach B?", the honest answer is *"I have no idea — I can't see my own usage."* Claude Code paints a `/usage` panel into your terminal, but the agent can't see TUI panels. Even less visible: the per-subagent attribution, the cache hit ratio, the per-turn growth — Claude has been flying blind on its own resource use.

`usage2` fixes that by reading Claude Code's **session transcript JSONL** — the file the CLI writes to `~/.claude/projects/<slug>/<session>.jsonl` as the API responds. Every assistant turn carries a `usage` block with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`. Every Task/Agent dispatch lands a `toolUseResult` record with the subagent's `totalTokens`, `agentType`, full `usage` breakdown, `toolStats`, and a prompt preview.

Sum these and you get authoritative per-action token attribution. No API key. No scraping. ~10ms per query.

The skill ALSO retains its earlier capability: a tmux-driven scrape of the built-in `/usage` panel for rolling 5h / 7d / Sonnet-only quota state with reset times. That's the answer to "how close to the hard limit am I?" — the transcript-level meter can't replace it because Anthropic's per-account quota windows aren't reconstructable from local data alone.

## Who this is for

People doing **long autonomous work with Claude** who want the agent itself to be able to:

- **Reason about token efficiency** — "I burned 50K tokens on that subagent dispatch; was it worth it?"
- **Run A/B experiments** — "Does this image at native resolution actually cost more tokens than resized to 1024×1024? Let me mark, run both, and check."
- **Self-throttle on long runs** — poll `/usage2 quick` every 10 minutes; pause when grand-total grows by 500K+ since the last check or cache-hit drops below 60%.
- **Attribute the cost back to the choice** — which subagent, with which prompt, burned how many tokens.
- **Pick model tiers based on remaining budget** — drop from Opus to Sonnet, or scale back parallel fan-outs, when the meter shows trouble.

This turns quota and token cost from external constraints the user has to babysit into signals the agent can reason about on its own.

## What it consists of

- **`meter.py`** — Python script that parses the session JSONL and reports token usage. Modes: `summary`, `quick`, `agents`, `mark`, `since`, `marks`, `drop`, `raw`, `quota`.
- **`capture.sh`** — Bash + tmux that captures the built-in `/usage` panel's rolling-window meters (the original v1 of this skill).
- **`SKILL.md`** — Claude Code skill descriptor.

Zero external dependencies. Pure stdlib Python 3 + bash + tmux. No API keys.

## Modes

| Mode | What it does | Cost |
|------|--------------|------|
| `summary` (default) | Full breakdown: main thread + subagents + efficiency signals | ~10ms |
| `quick` | One-line totals (cheap to poll in a loop) | ~10ms |
| `agents` | Per-subagent attribution only | ~10ms |
| `mark <name>` | Save a checkpoint at the current JSONL byte offset | ~1ms |
| `since <name>` | Report delta since a saved checkpoint (A/B comparisons) | ~10ms |
| `marks` | List saved checkpoints | ~1ms |
| `drop <name>` | Delete a checkpoint | ~1ms |
| `raw` | Dump aggregated counters as JSON (for downstream tools) | ~10ms |
| `quota` | Capture the rolling 5h / 7d / Sonnet-only meters (tmux scrape) | ~12s |

## The A/B workflow

This is the experimentation pattern Claude couldn't do before:

```bash
# Approach A
python3 meter.py mark approach-A
#   ... agent does the work (one or many tool calls / subagent spawns) ...
python3 meter.py since approach-A
#   → tokens consumed since the mark, with main + subagent split

# Approach B
python3 meter.py mark approach-B
#   ... agent does the work ...
python3 meter.py since approach-B
```

Claude reads the two deltas and tells you which approach cost less, by how much, and where the difference came from (fresh input? output verbosity? cache reuse?).

## Autonomous self-throttling pattern

Tell the agent at the start of a long autonomous run:

> Every 10 minutes, run `/usage2 quick`. If cache hit ratio drops below 60% or grand-total grows by more than 500K tokens since the last check, pause and report. If you need to know how close I am to my hard limit, run `/usage2 quota`.

`quick` is ~10ms, so polling is essentially free.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (the `claude` CLI on `$PATH`)
- Python 3.10+ (stdlib only)
- `tmux` (only needed for the `quota` mode — `brew install tmux`)
- bash 3+ (macOS default works)

## Install

```bash
git clone https://github.com/Loringtonian/usage2.git
mkdir -p ~/.claude/skills
cp -r usage2 ~/.claude/skills/usage2
chmod +x ~/.claude/skills/usage2/capture.sh ~/.claude/skills/usage2/meter.py
```

Then in any Claude Code session, type `/usage2` (or ask: "how many tokens has this session burned?", "which subagent dispatch cost the most?", "compare approach A vs approach B token-wise", etc.).

## How the meter works

1. Compute the project slug from `cwd`: leading slash → `-`, all `/` → `-`, all `_` → `-`. (Claude Code's naming convention.)
2. Find the most-recently-modified `*.jsonl` in `~/.claude/projects/<slug>/`. That's the current session.
3. Stream the JSONL. For each assistant record with `isSidechain` falsy, sum the `message.usage` block. For each user record with `toolUseResult.agentType`, sum `totalTokens` and pull the `usage`, `toolStats`, `agentType`, `prompt` for attribution.
4. Compute derived signals (cache hit ratio, output/input ratio, per-turn growth) and print.

`mark` saves the current file size as a byte offset. `since` reopens the file at that offset and runs the same aggregation on just the new lines.

## Caveats

- **The current in-flight turn is not yet in the JSONL.** Claude Code writes the assistant message + usage block after the turn completes. The meter is always one turn behind the live state. Fine for between-turn polling; not for in-turn metering.
- **Subagents are aggregated, not per-step.** `toolUseResult.totalTokens` gives the full cost of a subagent dispatch, but the parent transcript doesn't include the subagent's internal turn-by-turn detail. (Subagents may have their own JSONL files — the meter doesn't currently descend into those. Open improvement.)
- **Hooks aren't separately attributed.** A PostToolUse / PreCompact hook that injects context shows up in the next assistant turn's input count, but isn't broken out.
- **"Current session" = most-recently-modified JSONL** in the cwd's project slug dir. If multiple Claude Code instances are running in the same project, the meter reads whichever was written to most recently. Generally fine; edge cases exist.
- **Quota mode (the tmux scrape) spawns a real `claude` process** — uses no LLM tokens, but adds ~12s of latency.

## Comparison to similar approaches

- **`claude -p` `total_cost_usd`** — works only for subprocess (non-interactive) runs, gives a single per-invocation number, no breakdown.
- **`anthropic.count_tokens()`** — pre-counts input tokens for a planned API call, requires an API key (not available on Pro Max), only covers input not output.
- **OTEL telemetry** (`CLAUDE_CODE_ENABLE_TELEMETRY=1`) — Anthropic's official path for structured token metrics. More powerful but requires a collector (Prometheus / Grafana / etc.) and config. `usage2` is the zero-setup alternative.

## License

MIT. See `LICENSE`.

## Background

Built in May 2026 while trying to settle a token-efficiency question on the Personal Media Archive project (biographical-detail subagents on photos: native res vs 1024×1024?) and discovering the agent had no way to answer. The first version of `/usage2` (v1) was a tmux scrape of the built-in `/usage` panel. v2 (this version) adds the real value: per-action token attribution from the session transcript, including subagent costs. The trick — that Claude Code already writes authoritative per-message usage blocks to local JSONL files — generalizes: any other "give the agent visibility into something it currently can't see" problem in CC probably has a similar local-file answer.
