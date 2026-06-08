#!/usr/bin/env sh
set -eu

BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export TASK_MANAGER_BASE_DIR="$BASE_DIR"
cd "$BASE_DIR"
docker compose down
