#!/usr/bin/env bash
# Start the existing console app using one of the persistent model venvs.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

MODEL="menu"
if (( $# > 0 )); then
  case "$1" in
    --model)
      if (( $# != 2 )); then echo "Usage: --model qwen|moss" >&2; exit 2; fi
      MODEL="$2" ;;
    --doctor)
      exec python3 "$SCRIPT_DIR/doctor.py" --strict --require-models both ;;
    -h|--help)
      echo "Usage: bash runpod/start.sh [--model qwen|moss] [--doctor]"
      echo "Without --model, use the upstream TTS model/venv selection menu."
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
fi
case "$MODEL" in
  menu|qwen|moss) ;;
  *) echo "Unsupported model: $MODEL" >&2; exit 2 ;;
esac

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "The upstream console app requires an interactive terminal (RunPod SSH/web terminal)." >&2
  exit 1
fi

# Upstream app_paths.get_app_user_dir() uses Path.home(). RunPod's /root is
# ephemeral, so persist application preferences in the mounted data volume.
mkdir -p "$AUDIOBOOK_APP_HOME"
chmod 700 "$AUDIOBOOK_APP_HOME" || true
export HOME="$AUDIOBOOK_APP_HOME"

cd "$REPO_DIR"
if [[ "$MODEL" == "menu" ]]; then
  echo "Project directory for new audiobook projects: $AUDIOBOOK_PROJECTS_DIR"
  echo "Voice reference clips (keep private): $AUDIOBOOK_REFERENCES_DIR"
  exec python3 launch.py "$AUDIOBOOK_VENV_DIR"
fi

PYTHON="$AUDIOBOOK_VENV_DIR/$MODEL/bin/python"
if [[ ! -f "$AUDIOBOOK_VENV_DIR/$MODEL/.runpod-ready" || ! -x "$PYTHON" ]]; then
  echo "Model $MODEL is not installed. Run: bash runpod/setup.sh --models $MODEL" >&2
  exit 1
fi
echo "Launching TTS audiobook tool with $MODEL. Projects: $AUDIOBOOK_PROJECTS_DIR"
exec "$PYTHON" -m tts_audiobook_tool
