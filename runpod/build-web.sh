#!/usr/bin/env bash
# Safe, repeatable React build; can run while Python dependencies install.
# Called by autostart.sh and (as fallback) start-web.sh, never concurrently.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/env.sh"

if ! command -v npm >/dev/null 2>&1; then
  echo "Node/npm unavailable; run runpod/setup.sh first." >&2
  exit 1
fi
SRC="$REPO_DIR/web/src"
DIST="$REPO_DIR/web/dist/index.html"
NEED_BUILD=0
if [[ ! -s "$DIST" || ! -d "$REPO_DIR/web/node_modules" ]]; then
  NEED_BUILD=1
elif [[ -n "$(find "$SRC" -type f -newer "$DIST" -print -quit)" ]]; then
  NEED_BUILD=1
else
  for config in package.json tsconfig.json tsconfig.app.json vite.config.ts index.html; do
    if [[ -f "$REPO_DIR/web/$config" && "$REPO_DIR/web/$config" -nt "$DIST" ]]; then
      NEED_BUILD=1
      break
    fi
  done
fi
if (( ! NEED_BUILD )); then
  echo "React frontend is already built; skipping npm."
  exit 0
fi

# Cache persists across Pod restarts, unlike ephemeral /root/.npm.
export npm_config_cache="$AUDIOBOOK_DATA_ROOT/cache/npm"
mkdir -p "$npm_config_cache"
echo "Building German Audiobook Studio frontend."
(
  cd "$REPO_DIR/web"
  npm install --no-audit --no-fund --prefer-offline
  npm run build
)
test -s "$DIST" || { echo "Frontend build produced no dist/index.html." >&2; exit 1; }
echo "React frontend build complete."
