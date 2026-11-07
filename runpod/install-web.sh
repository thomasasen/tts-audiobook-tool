#!/usr/bin/env bash
# Persistent, hash-locked Web Studio runtime. Independent of TTS model venvs.
# Invoked after setup.sh --bootstrap-only; safe on repeat Pod starts.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/env.sh"
source "$SCRIPT_DIR/model-pins.env"

UV="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap/bin/uv"
VENV="$AUDIOBOOK_VENV_DIR/web"
LOCK="$REPO_DIR/web/requirements-runtime.lock.txt"
TORCH_LOCK="$REPO_DIR/web/torch-cpu.lock.txt"
READY="$VENV/.studio-web-runtime-ready"
RUNTIME_MANIFEST="$VENV/.studio-web-runtime-packages.sha256"

[[ -x "$UV" ]] || { echo "Web bootstrap missing uv; run setup.sh --bootstrap-only." >&2; exit 1; }
[[ -s "$LOCK" && -s "$TORCH_LOCK" ]] || {
  echo "Web runtime lock files are missing. Refusing an unpinned install." >&2
  exit 1
}
actual_uv_version="$("$UV" --version | awk '{print $2}')"
if [[ "$actual_uv_version" != "$UV_VERSION" ]]; then
  echo "uv version mismatch: expected $UV_VERSION, got $actual_uv_version." >&2
  exit 1
fi
mkdir -p "$AUDIOBOOK_VENV_DIR"
LOCK_SHA256="$(sha256sum "$LOCK" "$TORCH_LOCK" | sha256sum | awk '{print $1}')"
fingerprint="${LOCK_SHA256}-py-${WEB_PYTHON_VERSION}-uv-${UV_VERSION}"

runtime_freeze_hash() {
  "$UV" pip freeze --python "$VENV/bin/python" | LC_ALL=C sort | sha256sum | awk '{print $1}'
}

verify_web_runtime() {
  "$VENV/bin/python" - <<'PY'
import sys
import torch
assert sys.version_info[:3] == (3, 12, 16), sys.version
assert torch.__version__.split("+")[0] == "2.8.0", torch.__version__
# Never initialize CUDA from the web process. Thorsten owns the GPU runtime.
from importlib.metadata import version
assert version("faster-whisper") == "1.2.1"
assert version("ctranslate2") == "4.7.1"
from faster_whisper import WhisperModel
from fastapi import FastAPI
import av, soundfile, numpy
from tts_audiobook_tool.web_studio.app import app
from tts_audiobook_tool.project import Project
from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor
from tts_audiobook_tool.project_support.project_text_io_util import ProjectTextIOUtil
from tts_audiobook_tool.sound.audio_writers import AacWriter
assert isinstance(app, FastAPI)
print("Web runtime import check OK (no TTS weights, no GPU initialization).")
PY
}

if [[ -x "$VENV/bin/python" && -f "$READY" && "$(cat "$READY")" == "$fingerprint" &&
      -s "$RUNTIME_MANIFEST" ]] &&
   [[ "$(runtime_freeze_hash)" == "$(cat "$RUNTIME_MANIFEST")" ]] &&
   (cd "$REPO_DIR" && verify_web_runtime); then
  echo "Web runtime already installed and verified."
  exit 0
fi
if [[ -f "$READY" ]]; then
  echo "Existing Web runtime release or installed-package fingerprint differs from the lock." >&2
  echo "Refusing to mutate the active environment. Preserve it, create a fresh venv, then retry." >&2
  exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  "$UV" venv --python "$WEB_PYTHON_VERSION" --seed "$VENV"
else
  actual_python_version="$("$VENV/bin/python" -c 'import platform; print(platform.python_version())')"
  if [[ "$actual_python_version" != "$WEB_PYTHON_VERSION" ]]; then
    echo "Existing Web venv uses Python $actual_python_version; expected $WEB_PYTHON_VERSION." >&2
    echo "Preserve the old environment and create a fresh venv before retrying." >&2
    exit 1
  fi
fi

echo "Installing hash-locked Web Studio dependencies and the official CPU-only Torch wheel."
(
  source "$VENV/bin/activate"
  "$UV" pip install --python "$VENV/bin/python" --require-hashes --requirement "$LOCK"
  "$UV" pip install --python "$VENV/bin/python" --no-deps --require-hashes --requirement "$TORCH_LOCK"
)
(cd "$REPO_DIR" && verify_web_runtime)
runtime_hash="$(runtime_freeze_hash)"
printf '%s\n' "$runtime_hash" > "$RUNTIME_MANIFEST.tmp"
mv -f "$RUNTIME_MANIFEST.tmp" "$RUNTIME_MANIFEST"
printf '%s\n' "$fingerprint" > "$READY.tmp"
mv -f "$READY.tmp" "$READY"
echo "Web runtime ready."
