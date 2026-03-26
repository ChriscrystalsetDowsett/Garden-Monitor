#!/usr/bin/env bash
# start.sh — Start Garden Monitor inside a persistent tmux session.
#
# Usage:
#   bash scripts/start.sh          # start in background tmux session
#   bash scripts/start.sh --fg     # run in foreground (Ctrl-C to stop)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SESSION="garden-monitor"
PORT="$(python3 -c "import yaml; c=yaml.safe_load(open('$PROJECT_DIR/config/settings.yaml')); print(c['server']['port'])" 2>/dev/null || echo 8080)"

cd "$PROJECT_DIR"

if [[ "${1:-}" == "--fg" ]]; then
    echo "==> Starting Garden Monitor on port $PORT (foreground) …"
    exec python3 run.py
fi

# Background mode — use tmux so the session survives SSH disconnection
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "==> Garden Monitor is already running (tmux session: $SESSION)"
    echo "    Attach with: tmux attach -t $SESSION"
    exit 0
fi

tmux new-session -d -s "$SESSION" "cd '$PROJECT_DIR' && python3 run.py"
sleep 1

IP="$(hostname -I | awk '{print $1}')"
echo "==> Garden Monitor started on port $PORT"
echo "    Web UI:  http://$IP:$PORT"
echo "    Logs:    tmux attach -t $SESSION"
echo "    Stop:    bash scripts/stop.sh"
