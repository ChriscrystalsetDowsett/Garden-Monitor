#!/usr/bin/env bash
# stop.sh — Stop Garden Monitor gracefully.
set -euo pipefail

SESSION="garden-monitor"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux send-keys -t "$SESSION" C-c ""
    sleep 2
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    echo "==> Garden Monitor stopped (tmux session '$SESSION' closed)."
else
    # Fallback: kill any python3 run.py process
    if pkill -f "python3 run.py" 2>/dev/null; then
        echo "==> Garden Monitor process stopped."
    else
        echo "==> Garden Monitor does not appear to be running."
    fi
fi
