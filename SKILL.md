---
name: usage2
description: Pull the Claude Code built-in `/usage` panel into the conversation so the agent can actually see quota state. Spawns a throwaway `claude` instance in a detached tmux session, sends `/usage`, captures the rendered panel as plaintext, kills the session. Use when the user says "/usage2", "what's my usage", "how much quota left", "am I close to the limit", "check usage", "show me usage", or anytime the agent needs to reason about session/week/Sonnet-week quota in chat. The built-in `/usage` renders only in the user's TUI and is invisible to the agent — this skill is the workaround. No LLM call is made by the spawned instance; only the local /usage panel fetch.
allowed-tools: Bash
---

# /usage2

## What it does

Renders the built-in `/usage` panel — session, weekly, and Sonnet-only quota with reset times — into the agent's context. Built-in slash commands paint TUI panels that the agent cannot see; this skill captures one as plaintext via tmux.

## How to invoke

Run the capture script and print its stdout. That's the whole skill.

```bash
bash ${CLAUDE_SKILL_DIR}/capture.sh
```

Expected runtime: ~8–15 seconds (claude boot + /usage render + ~1s settle).

## How to interpret the output

The script returns the full tmux pane contents. Look for the `Usage` panel section, which contains three progress bars and reset times:

- **Current session** — rolling 5-hour quota window. Resets at the displayed local time.
- **Current week (all models)** — 7-day rolling window across Opus/Sonnet/Haiku.
- **Current week (Sonnet only)** — separate Sonnet-only meter (Anthropic-side limit).

Numbers are account-level — they reflect the user's actual quota regardless of which session ran the capture.

The panel also shows session-local Total cost / duration / usage, but those are zero for the throwaway instance and should be ignored. The skill output of interest is the three percentage bars + reset times.

## When to surface in chat

After capture, give the user a one-line summary, e.g.: `Session 63% (resets 8:10pm), Week 61%, Sonnet-week 53% (both reset May 18 3am)`. Include the raw panel only if asked for evidence.

If a number is concerning (e.g., session >80% with hours to go before reset), say so plainly so the user can decide whether to throttle.

## Caveats

- Spawns a real `claude` process. Boot is fast (empty project dir, no CLAUDE.md autoload) but adds ~10s of latency.
- Quota numbers in the panel are Anthropic-reported; the agent has no way to cross-check.
- The "What's contributing to your limits usage?" section often shows "Scanning local sessions..." because the capture is taken before that async scan finishes. The three top bars are reliable; the contributor breakdown isn't.
- If Anthropic restyles the `/usage` panel, the parser-free skill still works — it just dumps whatever the panel shows.

## Failure modes

- **`ERR: claude main UI did not render within ~15s`** — claude binary is slow to boot today or update prompt blocked. Re-run.
- **`WARN: usage panel keywords not detected`** — `/usage` didn't render in time; the dump shows whatever was on screen. Often still readable; if not, re-run.
