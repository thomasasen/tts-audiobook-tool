#!/usr/bin/env bash
# Start the existing FastAPI + built React studio on a RunPod GPU pod.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/env.sh"
PY="$AUDIOBOOK_VENV_DIR/web/bin/python"
mkdir -p "$AUDIOBOOK_DATA_ROOT" "$AUDIOBOOK_LOG_DIR"
chmod 700 "$AUDIOBOOK_DATA_ROOT"

# Direct/manual launches use the same idempotent preparation as the optimized
# autostart. It never installs Qwen/MOSS or downloads model weights.
if [[ ! -f "$AUDIOBOOK_VENV_DIR/web/.studio-web-runtime-ready" ]]; then
  echo "Web backend not prepared; installing minimal runtime."
  bash "$SCRIPT_DIR/install-web.sh"
fi
[[ -x "$PY" ]] || { echo "Web Python environment is unavailable." >&2; exit 1; }
bash "$SCRIPT_DIR/build-web.sh"


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
# Two independent Thorsten workers for NEW audiobook jobs by default.
# Operator may override this through RunPod Pod > Edit Pod > Environment Variables.
# Existing jobs retain their worker count stored in their immutable plan.
if [[ ! -v AUDIOBOOK_STUDIO_TTS_WORKERS ]]; then
  AUDIOBOOK_STUDIO_TTS_WORKERS=2
fi
case "$AUDIOBOOK_STUDIO_TTS_WORKERS" in
  1|2|3|4) ;;
  *) echo "AUDIOBOOK_STUDIO_TTS_WORKERS must be 1, 2, 3 or 4." >&2; exit 2 ;;
esac
export AUDIOBOOK_STUDIO_TTS_WORKERS
echo "Thorsten workers for new jobs: $AUDIOBOOK_STUDIO_TTS_WORKERS"
# Optional on-device CPU Whisper ASR checks. Installed/verified by install-web.sh.
# The first quality check downloads the chosen model onto the private pod.
# Set AUDIOBOOK_STUDIO_ASR_MODEL=off to disable it; skipped checks are visible.
if [[ ! -v AUDIOBOOK_STUDIO_ASR_MODEL ]]; then
  export AUDIOBOOK_STUDIO_ASR_MODEL=small
fi

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
