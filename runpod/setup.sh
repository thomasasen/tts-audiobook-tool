#!/usr/bin/env bash
# Reproducible RunPod Pod bootstrap for the existing audiobook application.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

usage() {
  cat <<'HELP'
Usage: bash runpod/setup.sh [--models qwen|moss|both] [--check-only]
  --models       Install selected local TTS environments (default: both).
  --check-only   Print diagnostics without installing anything.
  --help         Show this message.

Requires an NVIDIA GPU Pod, a mounted persistent /workspace volume, and root
or sudo for system packages. Python 3.12 and two isolated model environments
are installed under /workspace/audiobook-data, outside the git checkout.
HELP
}

MODELS="both"
CHECK_ONLY=0
while (( $# > 0 )); do
  case "$1" in
    --models)
      if (( $# < 2 )); then echo "Missing --models value" >&2; exit 2; fi
      MODELS="$2"; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$MODELS" in
  qwen|moss|both) ;;
  *) echo "Invalid models value: $MODELS" >&2; exit 2 ;;
esac

if (( CHECK_ONLY )); then
  exec python3 "$SCRIPT_DIR/doctor.py" --json
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This setup is intended for Linux RunPod Pods." >&2; exit 1
fi
if ! command -v mountpoint >/dev/null 2>&1; then
  echo "mountpoint is required to verify persistent storage." >&2; exit 1
fi
if [[ "${RUNPOD_ALLOW_UNMOUNTED:-0}" != "1" ]] && ! mountpoint -q "$RUNPOD_WORKSPACE"; then
  echo "Refusing to install: $RUNPOD_WORKSPACE is not a mounted persistent volume." >&2
  echo "Attach a RunPod volume at $RUNPOD_WORKSPACE before proceeding." >&2
  exit 1
fi
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; then
  echo "No working NVIDIA GPU detected (nvidia-smi). Choose a GPU Pod." >&2
  exit 1
fi

mkdir -p "$AUDIOBOOK_VENV_DIR" "$AUDIOBOOK_PROJECTS_DIR" \
  "$AUDIOBOOK_REFERENCES_DIR" "$AUDIOBOOK_LOG_DIR" "$AUDIOBOOK_APP_HOME" \
  "$HF_HUB_CACHE" "$TORCH_HOME" "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" \
  "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
chmod 700 "$AUDIOBOOK_REFERENCES_DIR" "$AUDIOBOOK_PROJECTS_DIR" "$AUDIOBOOK_APP_HOME" || true

LOG_FILE="$AUDIOBOOK_LOG_DIR/setup-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "RunPod setup: $(date -u +%FT%TZ) | models=$MODELS"
echo "Repo: $REPO_DIR"
echo "Persistent data: $AUDIOBOOK_DATA_ROOT"
echo "Setup log: $LOG_FILE"

install_system_packages() {
  local missing=0
  for cmd in git ffmpeg ffprobe tmux; do
    if ! command -v "$cmd" >/dev/null 2>&1; then missing=1; fi
  done
  if ! dpkg-query -W -f='${Status}' libportaudio2 2>/dev/null | grep -q 'install ok installed'; then
    missing=1
  fi
  if ! python3 -m venv --help >/dev/null 2>&1; then missing=1; fi
  if (( ! missing )); then
    echo "System packages already present."
    return
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Missing system packages and apt-get is unavailable." >&2; exit 1
  fi
  local -a apt=(apt-get)
  if (( EUID != 0 )); then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "Root or sudo is required to install system packages." >&2; exit 1
    fi
    apt=(sudo apt-get)
  fi
  "${apt[@]}" update
  DEBIAN_FRONTEND=noninteractive "${apt[@]}" install -y --no-install-recommends \
    ca-certificates git ffmpeg libportaudio2 python3-venv python3-pip tmux
}

install_system_packages

# uv is pinned and itself kept on the volume. Managed CPython 3.12 is also
# stored on the volume, so persistent venvs do not point to ephemeral /root.
UV_TOOL_DIR="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap"
UV="$UV_TOOL_DIR/bin/uv"
if [[ ! -x "$UV" ]]; then
  python3 -m venv "$UV_TOOL_DIR"
  "$UV_TOOL_DIR/bin/python" -m pip install --disable-pip-version-check 'uv==0.9.11'
fi
echo "uv: $("$UV" --version)"
"$UV" python install 3.12

verify_model_imports() {
  local model="$1" python="$2" version="$3"
  "$python" - "$model" "$version" <<'PY'
import importlib.util
import importlib.metadata
import sys
import torch

model, expected = sys.argv[1:]
if model == "qwen":
    assert importlib.util.find_spec("qwen_tts") is not None, "Missing package: qwen_tts"
else:
    # Match the upstream launcher catalog (local_module_test = "dist:moss-tts").
    assert importlib.metadata.distribution("moss-tts"), "Missing dist: moss-tts"
assert torch.__version__.split("+", 1)[0] == expected, (
    f"torch {torch.__version__}; expected {expected}"
)
assert sys.version_info[:2] == (3, 12), f"Expected Python 3.12, got {sys.version}"
print(f"Verified {model}: Python {sys.version.split()[0]}, torch {torch.__version__}")
PY
}

install_model() {
  local model="$1" requirements="$2" torch_version="$3"
  local env_dir="$AUDIOBOOK_VENV_DIR/$model"
  local req_path="$REPO_DIR/$requirements"
  local digest marker signature
  if [[ ! -f "$req_path" ]]; then
    echo "Missing requirements: $req_path" >&2; exit 1
  fi
  digest="$(sha256sum "$req_path" | awk '{print $1}')"
  marker="$env_dir/.runpod-ready"
  signature="python3.12/$model/$torch_version/$digest"
  if [[ -x "$env_dir/bin/python" && -f "$marker" ]] &&
     [[ "$(cat "$marker")" == "$signature" ]] &&
     verify_model_imports "$model" "$env_dir/bin/python" "$torch_version"; then
    echo "$model: already installed, no changes needed."
    return
  fi

  if [[ ! -x "$env_dir/bin/python" ]]; then
    "$UV" venv --python 3.12 --seed "$env_dir"
  fi
  rm -f "$marker"
  echo "Installing $model from $requirements (may download large CUDA wheels)."
  "$env_dir/bin/python" -m pip install --disable-pip-version-check \
    --requirement "$req_path"
  verify_model_imports "$model" "$env_dir/bin/python" "$torch_version"
  printf '%s\n' "$signature" > "$marker.tmp"
  mv "$marker.tmp" "$marker"
  echo "$model installation complete."
}

case "$MODELS" in
  qwen) install_model qwen requirements-qwen3tts.txt 2.8.0 ;;
  moss) install_model moss requirements-moss.txt 2.9.1 ;;
  both)
    install_model qwen requirements-qwen3tts.txt 2.8.0
    install_model moss requirements-moss.txt 2.9.1
    ;;
esac

echo "Installed requirements fingerprints:"
for model in qwen moss; do
  if [[ -f "$AUDIOBOOK_VENV_DIR/$model/.runpod-ready" ]]; then
    echo "  $model: $(cat "$AUDIOBOOK_VENV_DIR/$model/.runpod-ready")"
  fi
done
echo "Hardware and installed environment diagnostics:"
python3 "$SCRIPT_DIR/doctor.py" --strict --require-models "$MODELS"
echo "Setup complete. Run: bash runpod/start.sh"
