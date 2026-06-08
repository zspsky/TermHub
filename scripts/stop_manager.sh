#!/usr/bin/env sh
set -eu

BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PID_FILE="$BASE_DIR/data/task-manager.pid"
SOCKET="$BASE_DIR/data/sockets/task-manager.tmux"
SESSION="task-manager"

if tmux -S "$SOCKET" has-session -t "$SESSION" 2>/dev/null; then
  tmux -S "$SOCKET" kill-session -t "$SESSION"
  rm -f "$PID_FILE"
  echo "task manager stopped: $SESSION"
  exit 0
fi

if [ ! -f "$PID_FILE" ]; then
  echo "task manager is not running"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "task manager stopped: $PID"
else
  echo "task manager process not found: $PID"
fi
rm -f "$PID_FILE"
