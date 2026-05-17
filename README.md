# usage2

A tiny Claude Code skill that lets the agent **see** the built-in `/usage` panel — quota state, session/week/Sonnet-only meters, reset times — by capturing it as plaintext from a throwaway headless `claude` instance running inside tmux.

```
Session 63%        ███████████████████████████████▌
Week (all models)  ██████████████████████████████▌   61%
Week (Sonnet only) ██████████████████████████▌       53%
Resets: 8:10pm (session) · May 18 3am (week)
```

## Why this exists

Claude Code has a built-in `/usage` command that opens a TUI panel showing your rolling quota windows. It's useful — but **the agent can't see it.** Built-in slash commands paint pixels into your terminal; they don't expose their contents as tool output.

So if you ask the agent "am I close to my weekly limit?", the honest answer is *"I don't know — type `/usage` and tell me."* That's a worse experience than the agent being able to look for itself before deciding whether to dispatch a big parallel job, run a long-context skill, or call you back later.

`/usage2` fixes that. It spawns a second, throwaway `claude` process in a detached tmux session, sends it `/usage`, captures the rendered panel as plaintext, and prints it. The agent reads the plaintext like any other tool output and reasons about your quota in chat.

No LLM call is made by the spawned instance — `/usage` is a local panel that hits Anthropic's quota endpoint directly. So the workaround is essentially free (no token spend, no quota burn beyond what `/usage` itself would do if you typed it).

## Who this is for

People who use Claude Code (or a similar agent) to do long autonomous work and want the **agent itself** to be able to:

- **Self-assess token efficiency** as it works — "I've burned 40% of my 5-hour window on three failed attempts at this; is this approach worth continuing?"
- **Throttle itself** before hitting a wall — schedule `/usage2` on a loop and have the agent pause, hand off, or wind down at a self-imposed threshold instead of getting cut off mid-task.
- **Pick model tiers based on remaining budget** — drop from Opus to Sonnet, or from Sonnet to Haiku, when the corresponding weekly meter is nearing its ceiling.
- **Plan parallel fan-outs honestly** — "I have 8% of my Sonnet-week left; don't spawn 12 subagents."

This turns quota from an external constraint the user has to babysit into a signal the agent can reason about on its own.

## Common recipes

### Self-throttling on a long autonomous run

Tell the agent at the start of a session:

> Run `/usage2` every 10 minutes. If session usage goes above 75% — or if Sonnet-week goes above 90% — stop spawning new subagents, finish anything in flight, write a handoff note, and wait for me.

The agent then loops the skill on its own cadence (most agents have a `/loop` or scheduling primitive; if not, it can simply re-invoke between major steps) and treats the percentage thresholds as a soft brake.

### One-shot pre-flight check

Before kicking off something expensive (a parallel fan-out, a long context load, a big vision pass), the agent runs `/usage2` once and reports the numbers so you can both decide whether to proceed.

### Mid-task budget-aware decisions

When the agent hits a fork in the road — "should I dig deeper or hand this back?" — having current quota numbers in context lets it factor remaining budget into the call instead of guessing.

## What it consists of

Two files, ~140 lines total:

- **`capture.sh`** — bash script that runs the tmux dance: spawn `claude`, dismiss the trust prompt, send `/usage`, poll until the panel renders, capture the pane, strip ANSI, print.
- **`SKILL.md`** — Claude Code skill descriptor (frontmatter + agent-facing instructions on when to invoke and how to interpret the output).

No daemon. No config. No state between runs. No npm/Python/API keys.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (the `claude` CLI on `$PATH`)
- `tmux` — `brew install tmux` if you don't have it
- bash 3+ (macOS default works)

## Install

Drop the skill into your Claude Code skills directory:

```bash
git clone https://github.com/Loringtonian/usage2.git
mkdir -p ~/.claude/skills
cp -r usage2 ~/.claude/skills/usage2
chmod +x ~/.claude/skills/usage2/capture.sh
```

Then in any Claude Code session, type `/usage2` (or ask the agent "what's my quota?", "am I close to the limit?", etc.) and it'll capture the panel and summarize.

## How it works

1. Spawn `claude` inside a detached tmux session in an empty temp dir (no project `CLAUDE.md` autoload → fast boot).
2. Poll the pane for the "trust this folder?" prompt and send `Enter` to accept.
3. Poll for the main UI to render (status bar markers).
4. Send `/usage` + `Enter`.
5. Poll for the rendered panel keywords (`5-hour`, `7-day`, `week`, `Reset`, etc.).
6. `tmux capture-pane -p -S -500` to grab the visible pane plus a chunk of scrollback as plaintext.
7. Strip stray ANSI escapes with sed (belt-and-suspenders; tmux's default capture is already plaintext).
8. Kill the tmux session and clean up the temp dir on exit.

Total runtime: ~8–15 seconds, dominated by claude's boot.

The agent receives the full pane dump and is instructed (via `SKILL.md`) to pull out the three percentage bars + reset times and give you a one-line summary in chat. The raw panel is included only on request.

## Caveats

- The spawned `claude` is real — you'll see one extra short-lived process while it runs. It uses no LLM tokens, but it does briefly hold an MCP connection slot etc.
- If Anthropic restyles the `/usage` panel layout, the parser-free approach still works — the skill just dumps whatever is on screen and lets the agent figure it out.
- The panel's "What's contributing to your limits usage?" section often shows `Scanning local sessions…` because the capture is taken before that async scan finishes. The three top percentage bars are reliable; the contributor breakdown isn't.
- If the trust prompt has been disabled in your CC config, the script's prompt-polling step times out silently and continues — no harm done.

## License

MIT. See `LICENSE`.

## Background

Built in one session in May 2026 after asking Claude "what's my usage?" one too many times. Original `/usage` is Claude Code's built-in command; `usage2` is the agent-visible companion. The trick — driving an interactive TUI process from a sibling shell via tmux and reading the rendered screen as plaintext — generalizes to any other built-in panel an agent can't otherwise see.
