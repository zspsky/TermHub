#!/usr/bin/env sh
set -eu

VERSION="${TTYD_VERSION:-1.7.7}"
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TARGET_DIR="$BASE_DIR/tools/ttyd/$VERSION"
TARGET="$TARGET_DIR/ttyd"
URL="https://github.com/tsl0922/ttyd/releases/download/$VERSION/ttyd.x86_64"

mkdir -p "$TARGET_DIR"
curl -L -sS -o "$TARGET" "$URL"
chmod 0755 "$TARGET"
"$TARGET" --version

