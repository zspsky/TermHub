#!/usr/bin/env sh
set -eu

BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
LOG_DIR="$BASE_DIR/data/logs"
PID_FILE="$BASE_DIR/data/task-manager.pid"
SOCKET="$BASE_DIR/data/sockets/task-manager.tmux"
SESSION="task-manager"

mkdir -p "$LOG_DIR"

if tmux -S "$SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  echo "task manager already running in tmux: $SESSION"
  exit 0
fi

rm -f "$PID_FILE"
tmux -S "$SOCKET" new-session -d -s "$SESSION" -c "$BASE_DIR" \
  "python3 -u '$BASE_DIR/server.py' >> '$LOG_DIR/task-manager.log' 2>&1"
echo "task manager started in tmux: $SESSION"
