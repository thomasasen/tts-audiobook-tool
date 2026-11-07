#!/usr/bin/env bash
# Reproducible RunPod Pod bootstrap for the existing audiobook application.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
source "$SCRIPT_DIR/model-pins.env"

usage() {
  cat <<'HELP'
Usage: bash runpod/setup.sh [--models none|qwen|moss|both] [--check-only] [--bootstrap-only]
  --models       Optional legacy TUI models (none|qwen|moss|both; default: none).
  --check-only   Print diagnostics without installing anything.
  --bootstrap-only Prepare system packages, uv, and Python without large TTS dependencies.
  --help         Show this message.

Requires a mounted persistent /workspace volume and root or sudo for system
packages. --bootstrap-only prepares system utilities, uv and Python 3.12.
Qwen/MOSS are optional legacy TUI environments; Thorsten uses a separate venv.
HELP
}

MODELS="none"
CHECK_ONLY=0
BOOTSTRAP_ONLY=0
while (( $# > 0 )); do
  case "$1" in
    --models)
      if (( $# < 2 )); then echo "Missing --models value" >&2; exit 2; fi
      MODELS="$2"; shift 2 ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --bootstrap-only) BOOTSTRAP_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$MODELS" in
  none|qwen|moss|both) ;;
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
if (( ! BOOTSTRAP_ONLY )) && [[ "$MODELS" != none ]] &&
   { ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; }; then
  echo "No NVIDIA GPU found for optional legacy TUI models." >&2
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
  for cmd in git ffmpeg ffprobe tmux node npm; do
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
    ca-certificates git ffmpeg libportaudio2 python3-venv python3-pip tmux nodejs npm sox libsox-fmt-all
}

install_system_packages

# uv is pinned and itself kept on the volume. Managed CPython 3.12 is also
# stored on the volume, so persistent venvs do not point to ephemeral /root.
UV_TOOL_DIR="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap"
UV="$UV_TOOL_DIR/bin/uv"
if [[ ! -x "$UV" ]]; then
  python3 -m venv "$UV_TOOL_DIR"
  "$UV_TOOL_DIR/bin/python" -m pip install --disable-pip-version-check "uv==${UV_VERSION}"
fi
actual_uv_version="$("$UV" --version | awk '{print $2}')"
if [[ "$actual_uv_version" != "$UV_VERSION" ]]; then
  echo "uv version mismatch: expected $UV_VERSION, got $actual_uv_version." >&2
  exit 1
fi
echo "uv: $actual_uv_version"
"$UV" python install "$WEB_PYTHON_VERSION" "$THORSTEN_PYTHON_VERSION"
if (( BOOTSTRAP_ONLY )); then
  echo "RunPod bootstrap-only complete: OS packages, uv and managed Python $WEB_PYTHON_VERSION and $THORSTEN_PYTHON_VERSION ready."
  exit 0
fi

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
assert sys.version_info[:3] == (3, 12, 16), f"Expected Python 3.12.16, got {sys.version}"
print(f"Verified {model}: Python {sys.version.split()[0]}, torch {torch.__version__}")
PY
}

model_runtime_freeze_hash() {
  local python="$1"
  "$UV" pip freeze --python "$python" | LC_ALL=C sort | sha256sum | awk '{print $1}'
}

install_model() {
  local model="$1" requirements="$2" lock="$3" torch_version="$4"
  local env_dir="$AUDIOBOOK_VENV_DIR/$model"
  local req_path="$REPO_DIR/$requirements"
  local lock_path="$REPO_DIR/$lock"
  local digest marker signature runtime_manifest runtime_hash actual_python_version
  if [[ ! -f "$req_path" || ! -s "$lock_path" ]]; then
    echo "Missing source requirements or immutable lock for $model." >&2
    exit 1
  fi
  digest="$(sha256sum "$req_path" "$lock_path" | sha256sum | awk '{print $1}')"
  marker="$env_dir/.runpod-ready"
  runtime_manifest="$env_dir/.runpod-packages.sha256"
  signature="python${LEGACY_PYTHON_VERSION}/$model/$torch_version/uv-${UV_VERSION}/$digest"

  if [[ -x "$env_dir/bin/python" && -f "$marker" &&
        "$(cat "$marker")" == "$signature" && -s "$runtime_manifest" ]] &&
     [[ "$(model_runtime_freeze_hash "$env_dir/bin/python")" == "$(cat "$runtime_manifest")" ]] &&
     verify_model_imports "$model" "$env_dir/bin/python" "$torch_version"; then
    echo "$model: already installed and verified against the immutable lock."
    return
  fi
  if [[ -f "$marker" ]]; then
    echo "$model environment differs from its recorded lock/package fingerprint." >&2
    echo "Refusing to mutate it. Preserve the old venv and create a fresh one before retrying." >&2
    exit 1
  fi

  if [[ ! -x "$env_dir/bin/python" ]]; then
    "$UV" venv --python "$LEGACY_PYTHON_VERSION" --seed "$env_dir"
  else
    actual_python_version="$("$env_dir/bin/python" -c 'import platform; print(platform.python_version())')"
    if [[ "$actual_python_version" != "$LEGACY_PYTHON_VERSION" ]]; then
      echo "Existing $model venv uses Python $actual_python_version; expected $LEGACY_PYTHON_VERSION." >&2
      echo "Preserve the old environment and create a fresh venv before retrying." >&2
      exit 1
    fi
  fi
  echo "Installing $model from immutable lock $lock (may download large CUDA wheels)."
  "$UV" pip sync --python "$env_dir/bin/python" "$lock_path"
  verify_model_imports "$model" "$env_dir/bin/python" "$torch_version"
  runtime_hash="$(model_runtime_freeze_hash "$env_dir/bin/python")"
  printf '%s\n' "$runtime_hash" > "$runtime_manifest.tmp"
  chmod 600 "$runtime_manifest.tmp"
  mv -f "$runtime_manifest.tmp" "$runtime_manifest"
  printf '%s\n' "$signature" > "$marker.tmp"
  mv "$marker.tmp" "$marker"
  echo "$model installation complete."
}

case "$MODELS" in
  none) echo "No legacy TUI runtime requested; Web Studio uses its own lean venv." ;;
  qwen) install_model qwen requirements-qwen3tts.txt requirements-qwen3tts.lock.txt 2.8.0 ;;
  moss) install_model moss requirements-moss.txt requirements-moss.lock.txt 2.9.1 ;;
  both)
    install_model qwen requirements-qwen3tts.txt requirements-qwen3tts.lock.txt 2.8.0
    install_model moss requirements-moss.txt requirements-moss.lock.txt 2.9.1
    ;;
esac

echo "Installed requirements fingerprints:"
for model in qwen moss; do
  if [[ -f "$AUDIOBOOK_VENV_DIR/$model/.runpod-ready" ]]; then
    echo "  $model: $(cat "$AUDIOBOOK_VENV_DIR/$model/.runpod-ready")"
  fi
done
echo "Hardware and installed environment diagnostics:"
if [[ "$MODELS" == none ]]; then
  python3 "$SCRIPT_DIR/doctor.py" --json
else
  python3 "$SCRIPT_DIR/doctor.py" --strict --require-models "$MODELS"
fi
echo "Setup complete. Web Studio: bash runpod/install-web.sh && bash runpod/start-web.sh"
