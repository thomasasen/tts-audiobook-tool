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
  RUNPOD_TTS_COMMIT           Optional immutable 40-hex commit on master (release mode)
  RUNPOD_TTS_MODELS           none | qwen | moss | both (default: none; web-only)
  RUNPOD_TTS_AUTOSTART_MODEL  qwen | moss | menu (default: qwen)
  RUNPOD_TTS_START_TUI       0 (web only) | 1 (also launch legacy TUI); default: 0
  RUNPOD_TTS_SKIP_VOICE_INSTALL  1 skips new Thorsten downloads; default: 0
  RUNPOD_TTS_DOWNLOAD_WORKERS 1..8 concurrent files per Hugging Face download; default: 4

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
: "${RUNPOD_TTS_COMMIT:=}"
: "${RUNPOD_TTS_MODELS:=none}"
: "${RUNPOD_TTS_AUTOSTART_MODEL:=qwen}"
: "${RUNPOD_TTS_START_TUI:=0}"
: "${RUNPOD_TTS_SKIP_VOICE_INSTALL:=0}"
case "$RUNPOD_WORKSPACE" in
  /*) ;;
  *) printf 'RUNPOD_WORKSPACE must be absolute\n' >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_MODELS" in
  none|qwen|moss|both) ;;
  *) printf 'Invalid RUNPOD_TTS_MODELS: %s\n' "$RUNPOD_TTS_MODELS" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_START_TUI" in
  0|1) ;;
  *) printf 'Invalid RUNPOD_TTS_START_TUI: %s\n' "$RUNPOD_TTS_START_TUI" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_SKIP_VOICE_INSTALL" in
  0|1) ;;
  *) printf 'Invalid RUNPOD_TTS_SKIP_VOICE_INSTALL: %s\n' "$RUNPOD_TTS_SKIP_VOICE_INSTALL" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_AUTOSTART_MODEL" in
  qwen|moss|menu) ;;
  *) printf 'Invalid RUNPOD_TTS_AUTOSTART_MODEL: %s\n' "$RUNPOD_TTS_AUTOSTART_MODEL" >&2; exit 2 ;;
esac
case "$RUNPOD_TTS_BRANCH" in
  master|feature/german-audiobook-release-hardening|feature/german-audiobook-release-validation|feature/german-audiobook-studio-runpod) ;; # Explicit audited source allowlist.
  *) printf 'Unsupported deployment branch: %s\n' "$RUNPOD_TTS_BRANCH" >&2; exit 2 ;;
esac
if [[ -n "$RUNPOD_TTS_COMMIT" ]]; then
  if [[ ! "$RUNPOD_TTS_COMMIT" =~ ^[[:xdigit:]]{40}$ ]]; then
    echo "RUNPOD_TTS_COMMIT must be a full 40-character Git SHA." >&2; exit 2
  fi
  if [[ "$RUNPOD_TTS_BRANCH" != master ]]; then
    echo "Immutable release mode requires RUNPOD_TTS_BRANCH=master." >&2; exit 2
  fi
  RUNPOD_TTS_COMMIT="${RUNPOD_TTS_COMMIT,,}"
fi
if [[ "$RUNPOD_TTS_START_TUI" == 1 && "$RUNPOD_TTS_MODELS" != "both" && "$RUNPOD_TTS_AUTOSTART_MODEL" != "menu"
   && "$RUNPOD_TTS_MODELS" != "$RUNPOD_TTS_AUTOSTART_MODEL" ]]; then
  printf 'Startup model %s must be installed by RUNPOD_TTS_MODELS=%s\n' \
    "$RUNPOD_TTS_AUTOSTART_MODEL" "$RUNPOD_TTS_MODELS" >&2
  exit 2
fi
: "${RUNPOD_TTS_DOWNLOAD_WORKERS:=4}"
case "$RUNPOD_TTS_DOWNLOAD_WORKERS" in
  [1-8]) ;;
  *) echo "RUNPOD_TTS_DOWNLOAD_WORKERS must be 1..8." >&2; exit 2 ;;
esac
export RUNPOD_TTS_DOWNLOAD_WORKERS

DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-${RUNPOD_WORKSPACE%/}/audiobook-data}"
REPO_DIR="${RUNPOD_WORKSPACE%/}/tts-audiobook-tool"
if (( CHECK_ONLY )); then
  printf 'RunPod workspace: %s\nRepo: %s\nPersistent data: %s\nBranch: %s\nInstall: %s\nAutostart: %s\n' \
    "$RUNPOD_WORKSPACE" "$REPO_DIR" "$DATA_ROOT" "$RUNPOD_TTS_BRANCH" \
    "$RUNPOD_TTS_MODELS" "$RUNPOD_TTS_AUTOSTART_MODEL"
  printf 'Source mode: %s\n' "$(if [[ -n $RUNPOD_TTS_COMMIT ]]; then printf 'immutable %s' "$RUNPOD_TTS_COMMIT"; else printf 'development (floating branch)'; fi)"
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
  echo "Existing checkout found; enforcing clean source before deployment."
  # Ignore only untracked files during this safety check. Frontend builds
  # create web/dist/ inside the checkout, which previously caused every
  # subsequent Pod restart to silently skip all master updates.
  # Git still refuses a checkout/merge that would overwrite untracked files.
  if [[ -z "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]]; then
    git -C "$REPO_DIR" fetch --no-tags origin "$RUNPOD_TTS_BRANCH"
    if [[ -z "$RUNPOD_TTS_COMMIT" ]]; then
    if git -C "$REPO_DIR" show-ref --verify --quiet "refs/heads/$RUNPOD_TTS_BRANCH"; then
      git -C "$REPO_DIR" switch "$RUNPOD_TTS_BRANCH"
    else
      git -C "$REPO_DIR" switch -c "$RUNPOD_TTS_BRANCH" FETCH_HEAD
    fi
    git -C "$REPO_DIR" merge --ff-only FETCH_HEAD
    fi
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

if [[ -n "$RUNPOD_TTS_COMMIT" ]]; then
  # This ref is immutable. A restart must never float to a new master.
  # Reject arbitrary/external commits: only verified master history is allowed.
  git -C "$REPO_DIR" fetch --no-tags origin master
  if ! git -C "$REPO_DIR" cat-file -e "${RUNPOD_TTS_COMMIT}^{commit}" 2>/dev/null; then
    echo "ERROR: pinned release commit not found in the checked-out repository." >&2
    exit 1
  fi
  if ! git -C "$REPO_DIR" merge-base --is-ancestor "$RUNPOD_TTS_COMMIT" FETCH_HEAD; then
    echo "ERROR: pinned release commit is not in the fetched master history." >&2
    exit 1
  fi
  git -C "$REPO_DIR" switch --detach "$RUNPOD_TTS_COMMIT"
  if [[ "$(git -C "$REPO_DIR" rev-parse HEAD)" != "$RUNPOD_TTS_COMMIT" ]]; then
    echo "ERROR: source checkout differs from pinned release SHA." >&2
    exit 1
  fi
  echo "Immutable source release selected; no source upgrade on Pod restart."
fi

echo "Source commit: $(git -C "$REPO_DIR" rev-parse --short HEAD)"
echo "Boot mode: Web Studio + Thorsten. Legacy TUI models: $RUNPOD_TTS_MODELS."
echo "Step 1/5: Minimal host bootstrap (system tools, uv, Python)."
bash "$REPO_DIR/runpod/setup.sh" --models none --bootstrap-only
command -v tmux >/dev/null || { echo "ERROR: tmux missing after host bootstrap." >&2; exit 1; }

# Never allow a failed web startup to leave optional downloads running without
# supervision. The bash process waits for model downloads before it exits.
BUILD_PID=""
PREFETCH_PID=""
cleanup() {
  if [[ -n "$BUILD_PID" ]]; then kill "$BUILD_PID" 2>/dev/null || true; fi
  if [[ -n "$PREFETCH_PID" ]]; then kill "$PREFETCH_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT

echo "Step 2/5: Start independent frontend build, while preparing the web runtime."
WEB_BUILD_LOG="$DATA_ROOT/logs/web-build-$(date -u +%Y%m%dT%H%M%SZ).log"
bash "$REPO_DIR/runpod/build-web.sh" >"$WEB_BUILD_LOG" 2>&1 &
BUILD_PID=$!

# Check real CUDA allocation in the OFFICIAL container image. Do not use
# nvidia-smi or torch.cuda.is_available() alone to authorize large downloads.
echo "Checking host CUDA execution before any model downloads."
CUDA_SYSTEM_READY=0
if python3 "$REPO_DIR/runpod/gpu_preflight.py" --env system --json; then
  CUDA_SYSTEM_READY=1
  echo "System CUDA allocation OK."
else
  echo "WARNING: System CUDA allocation failed. Thorsten downloads are disabled."
  echo "Check RunPod GPU host; Web Studio remains available without TTS."
fi

# Network volumes are not unlimited. On a new 60 GB volume, allow at least
# 25 GiB headroom before concurrent model downloads. Reused verified models
# are handled by install-thorsten.sh even below this threshold.
MIN_FREE_GIB=25
FREE_BYTES=$(df -B1 --output=avail "$RUNPOD_WORKSPACE" | tail -n 1 | tr -d '[:space:]')
FREE_GIB=$(( FREE_BYTES / 1073741824 ))
echo "Persistent volume free: ${FREE_GIB} GiB."
PREFETCH_FAILED=0
NEED_PREFETCH=0
if [[ "$RUNPOD_TTS_SKIP_VOICE_INSTALL" == 1 ]]; then
  echo "No-download recovery requested; skipping Thorsten prefetch."
elif (( ! CUDA_SYSTEM_READY )); then
  echo "Skipping Thorsten prefetch: CUDA is not operational."
elif [[ -s "$DATA_ROOT/models/CosyVoice/.studio-ready" ]]; then
  echo "Cached Thorsten model found; installer will validate its SHA-256 manifest."
elif (( FREE_GIB < MIN_FREE_GIB )); then
  echo "WARNING: Only ${FREE_GIB} GiB free; skip first-time model download (minimum ${MIN_FREE_GIB} GiB)."
else
  NEED_PREFETCH=1
  PREFETCH_LOG="$DATA_ROOT/logs/thorsten-prefetch-$(date -u +%Y%m%dT%H%M%SZ).log"
  echo "Step 3/5: Background Thorsten download, max $RUNPOD_TTS_DOWNLOAD_WORKERS concurrent files."
  bash "$REPO_DIR/runpod/prefetch-thorsten.sh" >"$PREFETCH_LOG" 2>&1 &
  PREFETCH_PID=$!
fi

echo "Installing isolated Web Studio runtime (no Qwen or MOSS, no GPU initialization)."
if ! bash "$REPO_DIR/runpod/install-web.sh"; then
  echo "ERROR: Web runtime preparation failed; see latest setup log." >&2
  exit 1
fi
if ! wait "$BUILD_PID"; then
  echo "ERROR: React build failed. Tail of private frontend build log:" >&2
  tail -n 40 "$WEB_BUILD_LOG" >&2 || true
  BUILD_PID=""
  exit 1
fi
BUILD_PID=""
echo "Frontend build OK."

echo "Step 4/5: Start protected Web Studio before waiting for the Thorsten download."
WEB_PORT="${AUDIOBOOK_STUDIO_PORT:-7860}"
case "$WEB_PORT" in
  ''|*[!0-9]*) echo "ERROR: Invalid AUDIOBOOK_STUDIO_PORT." >&2; exit 2 ;;
esac

# tmux sessions can survive as an interactive shell after Uvicorn fails.
if curl -fsS --max-time 2 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1; then
  echo "Existing Web Studio already healthy."
else
  if tmux has-session -t audiobook-web 2>/dev/null; then
    echo "WARNING: Stale audiobook-web tmux session; restarting it."
    tmux kill-session -t audiobook-web
  fi
  tmux new-session -d -s audiobook-web -c "$REPO_DIR" \
    'bash runpod/start-web.sh; rc=$?; echo "Web server stopped (code $rc)."; exec bash'
fi

WEB_HEALTHY=0
for ((attempt=1; attempt<=90; attempt++)); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1; then
    WEB_HEALTHY=1
    break
  fi
  sleep 2
done
if (( ! WEB_HEALTHY )); then
  echo "ERROR: Web Studio did not respond on port $WEB_PORT. Webserver tail:" >&2
  tmux capture-pane -pt audiobook-web -S -60 2>&1 | tail -n 60 >&2 || true
  exit 1
fi
echo "Web studio health check OK (port $WEB_PORT)."
# The access token is shown ONLY on live Pod stdout (saved fd 3),
# never in the persistent autostart log captured by tee.
if [[ -n "${AUDIOBOOK_STUDIO_TOKEN:-}" ]]; then
  printf '%s\n' "Web Studio ready at HTTP $WEB_PORT. Use your private AUDIOBOOK_STUDIO_TOKEN (not logged)." >&3
else
  TOKEN_FILE="$DATA_ROOT/studio-access-token"
  if [[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" && -s "$TOKEN_FILE" ]]; then
    WEB_ACCESS_TOKEN="$(<"$TOKEN_FILE")"
    if (( ${#WEB_ACCESS_TOKEN} >= 32 )); then
      printf '\n%s\n%s\n%s\n%s\n\n' \
        '=== GERMAN AUDIOBOOK STUDIO: ZUGANGSSCHLÜSSEL ===' \
        "Weboberfläche: RunPod → Connect → HTTP $WEB_PORT" \
        "Zugangsschlüssel: $WEB_ACCESS_TOKEN" \
        'VERTRAULICH: Nur in privaten RunPod-Container-Logs anzeigen; nicht weitergeben.' >&3
    fi
    unset WEB_ACCESS_TOKEN
  else
    echo "WARNING: Web health OK but no generated token file found at $TOKEN_FILE."
  fi
fi

echo "Step 5/5: Finalize Thorsten installation only after prefetch has finished."
THORSTEN_INSTALL_OK=0
CUDA_THORSTEN_READY=0
if [[ "$RUNPOD_TTS_SKIP_VOICE_INSTALL" == 1 ]]; then
  echo "Skipping Thorsten installation as requested. No new model weights are downloaded."
elif (( ! CUDA_SYSTEM_READY )); then
  echo "WARNING: Torch CUDA allocation failed; no Thorsten model installation."
elif (( NEED_PREFETCH )); then
  if ! wait "$PREFETCH_PID"; then
    echo "WARNING: Thorsten prefetch failed. No immediate duplicate download attempt." >&2
    tail -n 35 "$PREFETCH_LOG" >&2 || true
    PREFETCH_FAILED=1
  fi
  PREFETCH_PID=""
fi

if [[ "$RUNPOD_TTS_SKIP_VOICE_INSTALL" == 0 && "$CUDA_SYSTEM_READY" == 1 && "$PREFETCH_FAILED" == 0 ]]; then
  if [[ -s "$DATA_ROOT/models/CosyVoice/.studio-ready" || "$NEED_PREFETCH" == 1 ]]; then
    if bash "$REPO_DIR/runpod/install-thorsten.sh"; then
      THORSTEN_INSTALL_OK=1
      if python3 "$REPO_DIR/runpod/gpu_preflight.py" --env thorsten --json; then
        CUDA_THORSTEN_READY=1
      else
        echo "WARNING: Model installed, but Thorsten CUDA allocation failed; inspect host."
      fi
    else
      echo "WARNING: Thorsten installation failed; web stays available without narration."
    fi
  else
    echo "WARNING: Not enough free space to begin Thorsten installation."
  fi
fi

if [[ "$RUNPOD_TTS_MODELS" != none ]]; then
  echo "Optional legacy TUI environment requested ($RUNPOD_TTS_MODELS)."
  if bash "$REPO_DIR/runpod/setup.sh" --models "$RUNPOD_TTS_MODELS"; then
    if [[ "$RUNPOD_TTS_START_TUI" == 1 ]]; then
      if ! tmux has-session -t audiobook 2>/dev/null; then
        session_command="bash runpod/start.sh"
        if [[ "$RUNPOD_TTS_AUTOSTART_MODEL" != menu ]]; then
          session_command+=" --model $RUNPOD_TTS_AUTOSTART_MODEL"
        fi
        session_command+='; rc=$?; echo "Audiobook TUI exited (code $rc)."; exec bash'
        tmux new-session -d -s audiobook -c "$REPO_DIR" "$session_command"
      fi
    fi
  else
    echo "WARNING: Optional legacy model install failed; Web Studio remains healthy."
  fi
fi
if [[ "$RUNPOD_TTS_START_TUI" == 1 && "$RUNPOD_TTS_MODELS" == none ]]; then
  echo "WARNING: Legacy TUI requested without its model; TUI not started."
fi
# Recheck HTTP after lengthy TTS installation, otherwise a server crash could
# misleadingly be reported as a complete and successful startup.
FINAL_WEB_STATUS=failed
if curl -fsS --max-time 5 "http://127.0.0.1:$WEB_PORT/api/health" >/dev/null 2>&1; then
  FINAL_WEB_STATUS=ready
else
  echo "ERROR: Web server became unreachable before final completion. Inspect audiobook-web." >&2
fi

VOICE_STATUS=not-ready
if (( THORSTEN_INSTALL_OK )); then VOICE_STATUS=installed; fi
SYS_CUDA_STATUS=failed
if (( CUDA_SYSTEM_READY )); then SYS_CUDA_STATUS=passed; fi
VOICE_CUDA_STATUS=not-tested
if (( THORSTEN_INSTALL_OK )); then VOICE_CUDA_STATUS=failed; fi
if (( CUDA_THORSTEN_READY )); then VOICE_CUDA_STATUS=passed; fi
TUI_STATUS="nicht gestartet (Standard)"
if [[ "$RUNPOD_TTS_START_TUI" == 1 ]] && tmux has-session -t audiobook 2>/dev/null; then
  TUI_STATUS="gestartet ($RUNPOD_TTS_AUTOSTART_MODEL)"
fi
GPU_LABEL="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || true)"
GPU_LABEL="${GPU_LABEL:-nicht ermittelbar}"

# Non-secret completion marker goes to the persistent autostart log.
echo "RunPod start steps complete: web=$FINAL_WEB_STATUS, Thorsten=$VOICE_STATUS, Thorsten CUDA=$VOICE_CUDA_STATUS."
echo "Access: RunPod -> Connect -> HTTP $WEB_PORT. Private final summary follows in CONTAINER logs."

# Full summary INCLUDING GENERATED TOKEN goes directly to the original Pod
# stdout (fd 3). It MUST NOT pass through the tee writing autostart-*.log.
RUNPOD_SUMMARY_WEB="$FINAL_WEB_STATUS" \
RUNPOD_SUMMARY_THORSTEN="$VOICE_STATUS" \
RUNPOD_SUMMARY_CUDA_SYSTEM="$SYS_CUDA_STATUS" \
RUNPOD_SUMMARY_CUDA_THORSTEN="$VOICE_CUDA_STATUS" \
RUNPOD_SUMMARY_GPU="$GPU_LABEL" \
RUNPOD_SUMMARY_COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD)" \
RUNPOD_SUMMARY_PORT="$WEB_PORT" \
RUNPOD_SUMMARY_DATA_ROOT="$DATA_ROOT" \
RUNPOD_SUMMARY_WORKSPACE="$RUNPOD_WORKSPACE" \
RUNPOD_SUMMARY_TUI="$TUI_STATUS" \
  bash "$REPO_DIR/runpod/print-start-summary.sh" >&3
exec 3>&-

if [[ "$FINAL_WEB_STATUS" != ready ]]; then
  echo "ERROR: RunPod bootstrap incomplete: Web Studio is not reachable." >&2
  exit 1
fi
