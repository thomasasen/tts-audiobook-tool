#!/usr/bin/env bash
# Generate immutable Python and Node dependency locks for the RunPod release.
# Run from any directory; output is placed in the supplied directory.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/model-pins.env"
OUTPUT_DIR="\${1:-$REPO_DIR/build/release-locks}"
UV="\${UV:-uv}"
command -v "$UV" >/dev/null 2>&1 || { echo "uv is required to compile release locks." >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required to create the frontend lock." >&2; exit 1; }
mkdir -p "$OUTPUT_DIR/web" "$OUTPUT_DIR/runpod"

"$UV" pip compile --generate-hashes --python-version 3.12 \
  --output-file "$OUTPUT_DIR/web/requirements-runtime.lock.txt" \
  "$REPO_DIR/web/requirements-runtime.txt"
"$UV" pip compile --generate-hashes --python-version 3.11 \
  --output-file "$OUTPUT_DIR/runpod/requirements-thorsten.lock.txt" \
  "$REPO_DIR/runpod/requirements-thorsten.in"
"$UV" pip compile --generate-hashes --python-version 3.12 \
  --output-file "$OUTPUT_DIR/requirements-qwen3tts.lock.txt" \
  "$REPO_DIR/requirements-qwen3tts.txt"
"$UV" pip compile --generate-hashes --python-version 3.12 \
  --output-file "$OUTPUT_DIR/requirements-moss.lock.txt" \
  "$REPO_DIR/requirements-moss.txt"

(
  cd "$REPO_DIR/web"
  npm install --package-lock-only --ignore-scripts --no-audit --no-fund
  cp package-lock.json "$OUTPUT_DIR/web/package-lock.json"
)
(
  cd "$OUTPUT_DIR"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
echo "Release dependency locks generated in $OUTPUT_DIR"
