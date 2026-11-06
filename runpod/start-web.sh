#!/usr/bin/env bash
# Start the existing FastAPI + built React studio on a RunPod GPU pod.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/env.sh"
PY="$AUDIOBOOK_VENV_DIR/qwen/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing Qwen/base Python environment. Run runpod/setup.sh --models qwen." >&2
  exit 1
fi

mkdir -p "$AUDIOBOOK_DATA_ROOT" "$AUDIOBOOK_LOG_DIR"
chmod 700 "$AUDIOBOOK_DATA_ROOT"
# Reuse project and dependency installations; no model download here.
WEB_READY="$AUDIOBOOK_DATA_ROOT/.studio-web-deps-ready"
WEB_REQ_FINGERPRINT="$(sha256sum "$REPO_DIR/web/requirements.txt" | cut -d' ' -f1)"
if [[ ! -f "$WEB_READY" || "$(cat "$WEB_READY")" != "$WEB_REQ_FINGERPRINT" ]]; then
  "$PY" -m pip install --disable-pip-version-check -r "$REPO_DIR/web/requirements.txt"
  printf '%s\n' "$WEB_REQ_FINGERPRINT" > "$WEB_READY"
fi

if [[ ! -f "$REPO_DIR/web/dist/index.html" || "$REPO_DIR/web/src/studio.tsx" -nt "$REPO_DIR/web/dist/index.html" ||
      "$REPO_DIR/web/src/style.css" -nt "$REPO_DIR/web/dist/index.html" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "Missing npm. Please install Node.js and npm on the pod." >&2
    exit 1
  fi
  (cd "$REPO_DIR/web" && npm install --no-audit --no-fund && npm run build)
fi

# Never put a bearer token into a command line or an ordinary Git/log file.
# Pod owner retrieves it via cat /workspace/audiobook-data/studio-access-token.
if [[ -z "${AUDIOBOOK_STUDIO_TOKEN:-}" ]]; then
  TOKEN_FILE="$AUDIOBOOK_DATA_ROOT/studio-access-token"
  if [[ ! -s "$TOKEN_FILE" ]]; then
    umask 077
    "$PY" -c 'import secrets; print(secrets.token_urlsafe(48))' > "$TOKEN_FILE"
  fi
  chmod 600 "$TOKEN_FILE"
  AUDIOBOOK_STUDIO_TOKEN="$(cat "$TOKEN_FILE")"
fi
if (( ${#AUDIOBOOK_STUDIO_TOKEN} < 32 )); then
  echo "AUDIOBOOK_STUDIO_TOKEN must have at least 32 characters." >&2
  exit 1
fi
export AUDIOBOOK_STUDIO_TOKEN
export AUDIOBOOK_STUDIO_DATA_DIR="$AUDIOBOOK_DATA_ROOT"

THORSTEN_ROOT="$AUDIOBOOK_DATA_ROOT/models/CosyVoice"
if [[ -s "$THORSTEN_ROOT/.studio-ready" &&
      -x "$AUDIOBOOK_VENV_DIR/thorsten/bin/python" &&
      -f "$THORSTEN_ROOT/infer_thorsten.py" ]]; then
  export AUDIOBOOK_THORSTEN_PYTHON="$AUDIOBOOK_VENV_DIR/thorsten/bin/python"
  export AUDIOBOOK_THORSTEN_SCRIPT="$THORSTEN_ROOT/infer_thorsten.py"
else
  echo "Thorsten not installed/validated: voice preview and generation are disabled."
fi

case "${AUDIOBOOK_STUDIO_PORT:-7860}" in
  ''|*[!0-9]*) echo "Invalid AUDIOBOOK_STUDIO_PORT" >&2; exit 2;;
esac
cd "$REPO_DIR"
exec "$PY" -m uvicorn tts_audiobook_tool.web_studio.app:app \
  --host 0.0.0.0 --port "${AUDIOBOOK_STUDIO_PORT:-7860}" --workers 1 \
  --proxy-headers --forwarded-allow-ips 127.0.0.1
