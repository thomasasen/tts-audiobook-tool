#!/usr/bin/env bash
# Persistent minimal web backend. Independent of Qwen/MOSS/CosyVoice GPU venvs.
# Invoked after setup.sh --bootstrap-only; safe on repeat Pod starts.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/env.sh"

UV="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap/bin/uv"
VENV="$AUDIOBOOK_VENV_DIR/web"
REQ="$REPO_DIR/web/requirements-runtime.txt"
CPU_TORCH_VERSION="2.8.0"
READY="$VENV/.studio-web-runtime-ready"

[[ -x "$UV" ]] || { echo "Web bootstrap missing uv; run setup.sh --bootstrap-only." >&2; exit 1; }
mkdir -p "$AUDIOBOOK_VENV_DIR"
fingerprint="$(sha256sum "$REQ" "$REPO_DIR/web/requirements.txt" | sha256sum | cut -d' ' -f1)-torchcpu${CPU_TORCH_VERSION}"

verify_web_runtime() {
  "$VENV/bin/python" - <<'PY'
import torch
assert torch.__version__.split("+")[0] == "2.8.0", torch.__version__
# Never initialize CUDA from the web process. Thorsten owns the GPU runtime.
from importlib.metadata import version
assert version("faster-whisper") == "1.2.1"
assert version("ctranslate2") == "4.7.1"
from faster_whisper import WhisperModel  # Must import in the isolated web venv
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

if [[ -x "$VENV/bin/python" && -f "$READY" && "$(cat "$READY")" == "$fingerprint" ]] &&
   (cd "$REPO_DIR" && verify_web_runtime); then
  echo "Web runtime already installed and verified."
  exit 0
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  "$UV" venv --python 3.12 --seed "$VENV"
fi
rm -f "$READY"
echo "Installing minimal Web Studio backend (CPU torch, no Qwen/MOSS CUDA wheels)."
# Match successful GitHub Actions setup: CPU Torch from PyTorch's CPU wheel index.
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-input \
  --index-url https://download.pytorch.org/whl/cpu "torch==${CPU_TORCH_VERSION}"
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-input \
  -r "$REQ"
(cd "$REPO_DIR" && verify_web_runtime)
printf '%s\n' "$fingerprint" > "$READY.tmp"
mv -f "$READY.tmp" "$READY"
echo "Web runtime ready."
