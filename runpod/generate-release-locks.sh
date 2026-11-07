#!/usr/bin/env bash
# Generate immutable Python and Node dependency locks for the RunPod release.
# Run from any directory; output is placed in the supplied directory.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/model-pins.env"
OUTPUT_DIR="$REPO_DIR/build/release-locks"
if [[ "$#" -gt 0 ]]; then OUTPUT_DIR="$1"; fi
UV="$(command -v uv || true)"
[[ -n "$UV" ]] || { echo "uv is required to compile release locks." >&2; exit 1; }
actual_uv_version="$("$UV" --version | awk '{print $2}')"
[[ "$actual_uv_version" == "$UV_VERSION" ]] || {
  echo "uv version mismatch: expected $UV_VERSION, got $actual_uv_version." >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || { echo "npm is required to create the frontend lock." >&2; exit 1; }
mkdir -p "$OUTPUT_DIR/web" "$OUTPUT_DIR/runpod"
"$UV" pip compile --generate-hashes --emit-index-url --index-url https://pypi.org/simple \
  --python "$WEB_PYTHON_VERSION" --python-version 3.12 \
  --output-file "$OUTPUT_DIR/runpod/uv-bootstrap.lock.txt" \
  "$REPO_DIR/runpod/uv-bootstrap.in"
OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"
# Reuse committed lock files as constraints so routine verification does not
# silently upgrade transitive packages merely because a new version appeared.
for lock in requirements-qwen3tts.lock.txt requirements-moss.lock.txt web/requirements-runtime.lock.txt web/torch-cpu.lock.txt runpod/uv-bootstrap.lock.txt; do
  if [[ -s "$REPO_DIR/$lock" ]]; then
    mkdir -p "$OUTPUT_DIR/$(dirname "$lock")"
    cp "$REPO_DIR/$lock" "$OUTPUT_DIR/$lock"
  fi
done
"$UV" python install "$WEB_PYTHON_VERSION" "$THORSTEN_PYTHON_VERSION"

"$UV" pip compile --generate-hashes --emit-index-url --index-url https://pypi.org/simple --python "$WEB_PYTHON_VERSION" --python-version 3.12 \
  --output-file "$OUTPUT_DIR/web/requirements-runtime.lock.txt" \
  "$REPO_DIR/web/requirements-runtime.txt"
"$UV" pip compile --generate-hashes --no-deps --emit-index-url \
  --index-url https://download.pytorch.org/whl/cpu --python "$WEB_PYTHON_VERSION" \
  --python-version 3.12 --output-file "$OUTPUT_DIR/web/torch-cpu.lock.txt" \
  "$REPO_DIR/web/torch-cpu.in"
if [[ -s "$REPO_DIR/runpod/locks/thorsten/uv.lock" ]]; then
  "$UV" lock --locked --project "$REPO_DIR/runpod/locks/thorsten" --python "$THORSTEN_PYTHON_VERSION"
else
  "$UV" lock --project "$REPO_DIR/runpod/locks/thorsten" --python "$THORSTEN_PYTHON_VERSION"
fi
mkdir -p "$OUTPUT_DIR/runpod/locks/thorsten"
cp "$REPO_DIR/runpod/locks/thorsten/uv.lock" "$OUTPUT_DIR/runpod/locks/thorsten/uv.lock"
"$UV" pip compile --generate-hashes --emit-index-url --index-url https://pypi.org/simple --python "$WEB_PYTHON_VERSION" --python-version 3.12 \
  --output-file "$OUTPUT_DIR/requirements-qwen3tts.lock.txt" \
  "$REPO_DIR/requirements-qwen3tts.txt"
"$UV" pip compile --generate-hashes --emit-index-url --index-url https://pypi.org/simple --python "$WEB_PYTHON_VERSION" --python-version 3.12 \
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
