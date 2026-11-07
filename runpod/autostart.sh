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
  RUNPOD_TTS_BRANCH           Git branch to deploy (default: master)
  RUNPOD_TTS_MODELS           qwen | moss | both (default: qwen)
  RUNPOD_TTS_AUTOSTART_MODEL  qwen | moss | menu (default: qwen)
  RUNPOD_TTS_START_TUI       0 (web only) | 1 (also launch legacy TUI); default: 0

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
: "${RUNPOD_TTS_BRANCH:=master}"
: "${RUNPOD_TTS_MODELS:=qwen}"
: "${RUNPOD_TTS_AUTOSTART_MODEL:=qwen}"
: "${RUNPOD_TTS_START_TUI:=0}"
case "$RUNPOD_WORKSPACE" in
  /*) ;;
  *) printf 'RUNPOD_WORKSPACE must be absolute\n' >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_MODELS" in
  qwen|moss|both) ;;
  *) printf 'Invalid RUNPOD_TTS_MODELS: %s\n' "$RUNPOD_TTS_MODELS" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_START_TUI" in
  0|1) ;;
  *) printf 'Invalid RUNPOD_TTS_START_TUI: %s\n' "$RUNPOD_TTS_START_TUI" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_AUTOSTART_MODEL" in
  qwen|moss|menu) ;;
  *) printf 'Invalid RUNPOD_TTS_AUTOSTART_MODEL: %s\n' "$RUNPOD_TTS_AUTOSTART_MODEL" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_BRANCH" in
  master|feature/german-audiobook-release-hardening|feature/german-audiobook-release-validation|feature/german-audiobook-studio-runpod) ;; # Explicit audited source allowlist.
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
# Save the original Pod stdout on fd 3 BEFORE tee captures installation logs.
# An auto-generated access token is shown only in the owner's live RunPod logs,
# never written to persistent autostart-*.log (or to Git).
exec 3>&1
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
  # Ignore only untracked files during this safety check. Frontend builds
  # create web/dist/ inside the checkout, which previously caused every
  # subsequent Pod restart to silently skip all master updates.
  # Git still refuses a checkout/merge that would overwrite untracked files.
  if [[ -z "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]]; then
    git -C "$REPO_DIR" fetch --no-tags origin "$RUNPOD_TTS_BRANCH"
    if git -C "$REPO_DIR" show-ref --verify --quiet "refs/heads/$RUNPOD_TTS_BRANCH"; then
      git -C "$REPO_DIR" switch "$RUNPOD_TTS_BRANCH"
    else
      git -C "$REPO_DIR" switch -c "$RUNPOD_TTS_BRANCH" FETCH_HEAD
    fi
    git -C "$REPO_DIR" merge --ff-only FETCH_HEAD
  else
    echo "ERROR: tracked source files have local modifications; refusing to deploy stale code." >&2
    git -C "$REPO_DIR" status --short --untracked-files=no >&2
    echo "Inspect the edits and preserve them before retrying. Nothing was overwritten." >&2
    exit 1
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
if [[ "$RUNPOD_TTS_START_TUI" == 1 ]]; then
  if tmux has-session -t audiobook 2>/dev/null; then
    echo "Existing legacy audiobook TUI session found; leaving it running."
  else
    session_command="bash runpod/start.sh"
    if [[ "$RUNPOD_TTS_AUTOSTART_MODEL" != menu ]]; then
      session_command+=" --model $RUNPOD_TTS_AUTOSTART_MODEL"
    fi
    session_command+='; rc=$?; echo "Audiobook app exited (code $rc)."; exec bash'
    tmux new-session -d -s audiobook -c "$REPO_DIR" "$session_command"
    echo "Created optional legacy TUI session 'audiobook'."
  fi
else
  echo "Web-only mode: no legacy TUI started; GPU reserved for Thorsten voice preview and books."
  if tmux has-session -t audiobook 2>/dev/null; then
    echo "WARNING: An older TUI session is still running; inspect it for GPU resource conflicts."
  fi
fi

echo "Installing German voice (optional until GPU acceptance)."
THORSTEN_INSTALL_OK=0
if bash "$REPO_DIR/runpod/install-thorsten.sh"; then
  THORSTEN_INSTALL_OK=1
else
  echo "WARNING: Thorsten installation failed. Web voice generation remains disabled."
fi
if tmux has-session -t audiobook-web 2>/dev/null; then
  echo "Existing web studio session left running."
else
  tmux new-session -d -s audiobook-web -c "$REPO_DIR" "bash runpod/start-web.sh; rc=\$?; echo Web server stopped: \$rc; exec bash"
  echo "Started the web studio in tmux session audiobook-web (port 7860)."
fi

# Announce the *generated* login code only after FastAPI responds successfully.
# Print to original container stdout (fd 3), bypassing the persistent tee log.
# Never display a user-provided AUDIOBOOK_STUDIO_TOKEN secret.
WEB_HEALTHY=0
WEB_PORT="${AUDIOBOOK_STUDIO_PORT:-7860}"
if [[ "$WEB_PORT" =~ ^[0-9]+$ ]]; then
  for (( attempt=1; attempt<=45; attempt++ )); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1; then
      WEB_HEALTHY=1
      break
    fi
    sleep 2
  done
else
  echo "WARNING: Invalid AUDIOBOOK_STUDIO_PORT; cannot check web readiness."
fi

if (( WEB_HEALTHY )); then
  echo "Web studio health check OK (port $WEB_PORT)."
  if [[ -n "${AUDIOBOOK_STUDIO_TOKEN:-}" ]]; then
    printf '%s\n' "German Audiobook Studio: Weboberfläche bereit. Verwende deinen selbst gesetzten Zugangsschlüssel (er wird nicht im Log angezeigt)." >&3
  else
    TOKEN_FILE="$DATA_ROOT/studio-access-token"
    if [[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" && -s "$TOKEN_FILE" ]]; then
      # Read once; the access code must not appear in the persistent log.
      WEB_ACCESS_TOKEN="$(<"$TOKEN_FILE")"
      if (( ${#WEB_ACCESS_TOKEN} >= 32 )); then
        printf '\n%s\n%s\n%s\n%s\n\n' \
          '=== GERMAN AUDIOBOOK STUDIO: ZUGANGSSCHLÜSSEL ===' \
          "Weboberfläche: RunPod → Connect → HTTP $WEB_PORT" \
          "Zugangsschlüssel: $WEB_ACCESS_TOKEN" \
          'VERTRAULICH: Nur in privaten RunPod-Container-Logs anzeigen; nicht weitergeben.' >&3
      else
        echo "WARNING: Studio token file contains an invalid token; inspect via private Pod terminal." 
      fi
      unset WEB_ACCESS_TOKEN
    else
      echo "WARNING: Web studio is healthy, but no generated token file found at $TOKEN_FILE."
    fi
  fi
else
  echo "WARNING: Web studio did not respond on port $WEB_PORT. Check tmux attach -t audiobook-web."
fi
exec 3>&-

if (( THORSTEN_INSTALL_OK )); then
  echo "=== Web studio launch requested; Thorsten installed (GPU hearing test still mandatory) ==="
else
  echo "=== Web studio launch requested; THORSTEN NOT READY, narration disabled ==="
fi
echo "Web log: tmux attach -t audiobook-web | Optional TUI (if enabled): tmux attach -t audiobook"
echo "Optional: bash $REPO_DIR/runpod/start.sh --doctor"
echo "Models, settings, reference voices and projects remain under $DATA_ROOT"
