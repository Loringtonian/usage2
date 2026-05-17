#!/usr/bin/env bash
# /usage2 driver: spawn a headless `claude`, send `/usage`, capture the panel
# as plaintext via tmux, then kill the session.
#
# Stdout = rendered /usage panel text.
# Stderr = diagnostics if anything goes wrong.

set -u
set -o pipefail

SESSION="usage2_$$_$(date +%s)"
TMP_PROJ=$(mktemp -d -t usage2)
OUT=$(mktemp -t usage2_out)

cleanup() {
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  rm -rf "$TMP_PROJ" "$OUT"
}
trap cleanup EXIT

poll_for() {
  # poll_for <pattern> <max_iters_at_0.3s_each>
  local pat="$1" max="$2" i
  for i in $(seq 1 "$max"); do
    if tmux capture-pane -t "$SESSION" -p 2>/dev/null | grep -qE "$pat"; then
      return 0
    fi
    sleep 0.3
  done
  return 1
}

# Empty project dir keeps boot fast (no CLAUDE.md autoload), but triggers a
# "trust this folder?" prompt we have to dismiss.
tmux new-session -d -s "$SESSION" -x 220 -y 70 -c "$TMP_PROJ"
tmux send-keys -t "$SESSION" "claude" Enter

# Wait for trust prompt, accept with Enter (default is option 1 = trust).
if poll_for "trust this folder|Accessing workspace" 30; then
  tmux send-keys -t "$SESSION" Enter
else
  # Some installs auto-trust; that's fine, just continue.
  :
fi

# Wait for main UI (status bar or input border).
if ! poll_for "Opus|Sonnet|Haiku|ctx:|⏵⏵|auto mode on" 50; then
  echo "ERR: claude main UI did not render within ~15s" >&2
  tmux capture-pane -t "$SESSION" -p -S -200 >&2
  exit 1
fi

# Type /usage and submit
tmux send-keys -t "$SESSION" "/usage" Enter

# /usage may render a TUI panel that takes a moment + may need the response to
# come back from the quota endpoint. Poll for usage-panel keywords.
if ! poll_for "5.?hour|7.?day|week|Usage|Plan|Reset" 25; then
  echo "WARN: usage panel keywords not detected; capturing what's on screen anyway" >&2
fi

sleep 1.0

# Capture visible pane + a healthy chunk of scrollback
tmux capture-pane -t "$SESSION" -p -S -500 > "$OUT"

# Strip any stray ANSI (tmux default is plaintext but be safe)
sed -E $'s/\x1b\\[[0-9;?]*[a-zA-Z]//g' "$OUT"
