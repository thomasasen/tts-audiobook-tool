#!/usr/bin/env bash
# Automatic RunPod bootstrap. Intended for the official PyTorch Pod Docker Command:
#   /start.sh & ... download and execute this script ...; wait
# /start.sh remains owned by the outer command so SSH/Jupyter stay available.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: bash runpod/autostart.sh [--check-only] [--help]

Environment variables:
  RUNPOD_WORKSPACE            Mounted network volume (default: /workspace)
  RUNPOD_TTS_BRANCH           Git branch to deploy (default: feature/runpod-p0)
  RUNPOD_TTS_MODELS           qwen | moss | both (default: both)
  RUNPOD_TTS_AUTOSTART_MODEL  qwen | moss | menu (default: qwen)

This script does NOT run the standard RunPod /start.sh service. In the Pod
Docker Command, start /start.sh in the background before invoking it.
For the exact command see runpod/README.md.
EOF
}

CHECK_ONLY=0
while (( $# > 0 )); do
  case "$1" in
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

: "${RUNPOD_WORKSPACE:=/workspace}"
: "${RUNPOD_TTS_BRANCH:=feature/runpod-p0}"
: "${RUNPOD_TTS_MODELS:=both}"
: "${RUNPOD_TTS_AUTOSTART_MODEL:=qwen}"
case "$RUNPOD_WORKSPACE" in
  /*) ;;
  *) printf 'RUNPOD_WORKSPACE must be absolute\n' >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_MODELS" in
  qwen|moss|both) ;;
  *) printf 'Invalid RUNPOD_TTS_MODELS: %s\n' "$RUNPOD_TTS_MODELS" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_AUTOSTART_MODEL" in
  qwen|moss|menu) ;;
  *) printf 'Invalid RUNPOD_TTS_AUTOSTART_MODEL: %s\n' "$RUNPOD_TTS_AUTOSTART_MODEL" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_BRANCH" in
  feature/runpod-p0) ;; # Use the audited deployment branch during P0.
  *) printf 'Unsupported deployment branch: %s\n' "$RUNPOD_TTS_BRANCH" >&2; exit 2 ;;
esac
if [[ "$RUNPOD_TTS_MODELS" != "both" && "$RUNPOD_TTS_AUTOSTART_MODEL" != "menu"
   && "$RUNPOD_TTS_MODELS" != "$RUNPOD_TTS_AUTOSTART_MODEL" ]]; then
  printf 'Startup model %s must be installed by RUNPOD_TTS_MODELS=%s\n' \
    "$RUNPOD_TTS_AUTOSTART_MODEL" "$RUNPOD_TTS_MODELS" >&2
  exit 2
fi

DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-${RUNPOD_WORKSPACE%/}/audiobook-data}"
REPO_DIR="${RUNPOD_WORKSPACE%/}/tts-audiobook-tool"
if (( CHECK_ONLY )); then
  printf 'RunPod workspace: %s\nRepo: %s\nPersistent data: %s\nBranch: %s\nInstall: %s\nAutostart: %s\n' \
    "$RUNPOD_WORKSPACE" "$REPO_DIR" "$DATA_ROOT" "$RUNPOD_TTS_BRANCH" \
    "$RUNPOD_TTS_MODELS" "$RUNPOD_TTS_AUTOSTART_MODEL"
  exit 0
fi

if [[ "$(uname -s)" != Linux ]]; then
  echo "RunPod autostart requires Linux." >&2; exit 1
fi
if ! command -v mountpoint >/dev/null 2>&1 ||
   ! mountpoint -q "$RUNPOD_WORKSPACE"; then
  echo "ERROR: $RUNPOD_WORKSPACE is not a persistent mount. Attach a RunPod Network Volume first." >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is missing. Select the official RunPod PyTorch template." >&2
  exit 1
fi

mkdir -p "$DATA_ROOT/logs"
LOG_FILE="$DATA_ROOT/logs/autostart-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'status=$?; echo "RunPod audiobook bootstrap FAILED (exit $status). Log: $LOG_FILE" >&2' ERR

echo "=== German audiobook RunPod startup ==="
echo "UTC: $(date -u +%FT%TZ)"
echo "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo unavailable)"
echo "Repo: $REPO_DIR | branch: $RUNPOD_TTS_BRANCH"
echo "Persistent data: $DATA_ROOT | log: $LOG_FILE"
echo "Models: $RUNPOD_TTS_MODELS | terminal auto-start: $RUNPOD_TTS_AUTOSTART_MODEL"

if [[ -d "$REPO_DIR/.git" ]]; then
  echo "Existing checkout found; using fast-forward-only updates."
  if [[ -z "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
    git -C "$REPO_DIR" fetch --no-tags origin "$RUNPOD_TTS_BRANCH"
    if git -C "$REPO_DIR" show-ref --verify --quiet "refs/heads/$RUNPOD_TTS_BRANCH"; then
      git -C "$REPO_DIR" switch "$RUNPOD_TTS_BRANCH"
    else
      git -C "$REPO_DIR" switch -c "$RUNPOD_TTS_BRANCH" FETCH_HEAD
    fi
    git -C "$REPO_DIR" merge --ff-only FETCH_HEAD
  else
    echo "WARNING: uncommitted modifications detected; source checkout will not be changed."
    if [[ "$(git -C "$REPO_DIR" branch --show-current)" != "$RUNPOD_TTS_BRANCH" ]]; then
      echo "ERROR: dirty checkout is on the wrong branch; refusing to deploy." >&2
      exit 1
    fi
  fi
elif [[ -e "$REPO_DIR" ]]; then
  echo "ERROR: $REPO_DIR exists but is not a Git checkout. Refusing to overwrite it." >&2
  exit 1
else
  git clone --single-branch --branch "$RUNPOD_TTS_BRANCH" \
    https://github.com/thomasasen/tts-audiobook-tool.git "$REPO_DIR"
fi

echo "Source commit: $(git -C "$REPO_DIR" rev-parse --short HEAD)"
echo "Installing required environments. No book data or voice samples are downloaded."
bash "$REPO_DIR/runpod/setup.sh" --models "$RUNPOD_TTS_MODELS"

if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux missing after setup." >&2
  exit 1
fi
if tmux has-session -t audiobook 2>/dev/null; then
  echo "Existing audiobook tmux session found; leaving it running."
else
  session_command="bash runpod/start.sh"
  if [[ "$RUNPOD_TTS_AUTOSTART_MODEL" != menu ]]; then
    session_command+=" --model $RUNPOD_TTS_AUTOSTART_MODEL"
  fi
  # Keep the tmux session open after a TUI error, to expose logs/messages.
  session_command+='; rc=$?; echo "Audiobook app exited (code $rc)."; exec bash'
  tmux new-session -d -s audiobook -c "$REPO_DIR" "$session_command"
  echo "Created tmux session 'audiobook'."
fi

echo "=== Ready ==="
echo "Open a RunPod SSH/Web Terminal, then: tmux attach -t audiobook"
echo "Optional: bash $REPO_DIR/runpod/start.sh --doctor"
echo "Models, settings, reference voices and projects remain under $DATA_ROOT"
