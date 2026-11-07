#!/usr/bin/env bash
# Curated native German Thorsten CosyVoice3 model, isolated from Qwen/MOSS venvs.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"
source "$SCRIPT_DIR/model-pins.env"
source "$SCRIPT_DIR/model-source.sh"
MODEL_ROOT="$AUDIOBOOK_DATA_ROOT/models/CosyVoice"
MODEL_DIR="$MODEL_ROOT/pretrained_models/CosyVoice3-0.5B"
VENV="$AUDIOBOOK_VENV_DIR/thorsten"
UV="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap/bin/uv"
VERSION="$MODEL_RELEASE_ID"
CHECKSUMS="$AUDIOBOOK_DATA_ROOT/models/thorsten-model-sha256.txt"
mkdir -p "$AUDIOBOOK_DATA_ROOT/models" "$AUDIOBOOK_VENV_DIR"
if [[ ! -x "$UV" ]]; then
  echo "Run bash runpod/setup.sh --bootstrap-only before installing Thorsten." >&2
  exit 1
fi
# CosyVoice's pinned Lightning 2.2.4 still imports pkg_resources.
# setuptools 82+ removed that module. Repair *only* the isolated Thorsten
# runtime, including already verified persistent model installs, so a Pod
# restart does not download or reinstall multi-gigabyte model weights.
ensure_thorsten_pkg_resources() {
  [[ -x "$VENV/bin/python" ]] || return 0
  if "$VENV/bin/python" -W ignore::DeprecationWarning -c 'import pkg_resources' >/dev/null 2>&1; then
    return 0
  fi
  echo "Repairing Thorsten runtime: pkg_resources unavailable; pinning setuptools==80.9.0."
  "$VENV/bin/python" -m pip install --disable-pip-version-check --no-input "setuptools==80.9.0"
  "$VENV/bin/python" -W ignore::DeprecationWarning -c 'import pkg_resources' >/dev/null || {
    echo "Thorsten runtime repair failed: pkg_resources is still unavailable." >&2
    return 1
  }
}

# Must execute BEFORE the .studio-ready fast path. The weights may be valid
# even though a later pip upgrade made the installed runtime unusable.
ensure_thorsten_pkg_resources
if [[ -f "$MODEL_ROOT/.studio-ready" && "$(cat "$MODEL_ROOT/.studio-ready")" == "$VERSION" &&
      -x "$VENV/bin/python" && -s "$MODEL_DIR/llm.pt" &&
      -s "$MODEL_DIR/flow.pt" && -s "$MODEL_DIR/spk2info.pt" &&
      -s "$MODEL_DIR/hift.pt" && -s "$MODEL_DIR/infer_thorsten.py" &&
      -s "$MODEL_ROOT/infer_thorsten.py" && -s "$CHECKSUMS" ]] &&
   verify_cosyvoice_checkout "$MODEL_ROOT" &&
   (cd "$MODEL_DIR" && sha256sum --check --status "$CHECKSUMS") &&
   cmp -s "$MODEL_DIR/infer_thorsten.py" "$MODEL_ROOT/infer_thorsten.py"; then
  echo "Thorsten CosyVoice3 weights verified against persistent SHA-256 manifest."
  exit 0
fi
if [[ -f "$MODEL_ROOT/.studio-ready" ]]; then
  # A previously trusted cached model was modified, is incomplete, or has
  # a stale version. Do NOT silently rebaseline corrupted weights: model
  # downloads can reuse files that already exist in the Hugging Face cache.
  echo "Thorsten cache integrity/version failed. Refusing to trust or silently rebaseline weights." >&2
  echo "Inspect and quarantine the cached model and checksum manifest before a clean reinstall." >&2
  exit 1
fi

# The upstream Thorsten model card recommends Python 3.10/3.11 and pins the
# CosyVoice framework commit. Do NOT try to share Python 3.12 Qwen/MOSS deps.
"$UV" python install "$THORSTEN_PYTHON_VERSION"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$UV" venv --python "$THORSTEN_PYTHON_VERSION" --seed "$VENV"
else
  actual_python_version="$("$VENV/bin/python" -c 'import platform; print(platform.python_version())')"
  if [[ "$actual_python_version" != "$THORSTEN_PYTHON_VERSION" ]]; then
    echo "Existing Thorsten venv uses Python $actual_python_version; expected $THORSTEN_PYTHON_VERSION." >&2
    echo "Preserve the old environment and create a fresh venv before retrying; no automatic deletion is performed." >&2
    exit 1
  fi
fi
ensure_cosyvoice_checkout "$MODEL_ROOT"
if command -v apt-get >/dev/null 2>&1 && [[ "$EUID" -eq 0 ]]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sox libsox-fmt-all
fi
# Do not upgrade setuptools: v82+ removes pkg_resources needed by Lightning.
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-input "setuptools==80.9.0"
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-input \
  "openai-whisper==20231117" "huggingface_hub==${HF_HUB_VERSION}"
grep -v '^openai-whisper' "$MODEL_ROOT/requirements.txt" > "$AUDIOBOOK_DATA_ROOT/cosyvoice-requirements.txt"
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$AUDIOBOOK_DATA_ROOT/cosyvoice-requirements.txt"
# Guard against transitive upgrades that could remove pkg_resources again.
ensure_thorsten_pkg_resources

# Download into persistent paths and overlay *only* the authored fine-tune
# weights and embedding. The base vocoder is preserved.
# The common RunPod image sets HF_HUB_ENABLE_HF_TRANSFER=1 without providing
# hf_transfer in this separate Python 3.11 venv. env.sh forces standard HTTP.
echo "Thorsten model download mode: standard Hugging Face HTTP (HF_HUB_ENABLE_HF_TRANSFER=$HF_HUB_ENABLE_HF_TRANSFER)"
PREFETCH_VERSION="$MODEL_ROOT/.prefetch-complete"
PREFETCH_SUMS="$MODEL_ROOT/.prefetch-sha256.txt"
if [[ -f "$PREFETCH_VERSION" ]]; then
  if [[ "$(cat "$PREFETCH_VERSION")" != "$VERSION" || ! -s "$PREFETCH_SUMS" ]] ||
     ! (cd "$MODEL_DIR" && sha256sum --check --status "$PREFETCH_SUMS"); then
    echo "Prefetched model checksum/version invalid; refusing to trust/rebaseline." >&2
    exit 1
  fi
  echo "Using completed, SHA-256 checked Thorsten prefetch: skipping both model downloads."
else
  # No prior prefetch: the regular installer is still valid standalone.
  # Base THEN fine-tune is mandatory; both snapshot repos contain llm.pt etc.
  : "${RUNPOD_TTS_DOWNLOAD_WORKERS:=4}"
  if [[ ! "$RUNPOD_TTS_DOWNLOAD_WORKERS" =~ ^[1-8]$ ]]; then
    echo "RUNPOD_TTS_DOWNLOAD_WORKERS must be 1..8." >&2
    exit 2
  fi
  "$VENV/bin/hf" download "$BASE_MODEL_ID" --revision "$BASE_MODEL_REVISION" \
    --local-dir "$MODEL_DIR" --max-workers "$RUNPOD_TTS_DOWNLOAD_WORKERS"
  "$VENV/bin/hf" download "$THORSTEN_MODEL_ID" llm.pt flow.pt spk2info.pt infer_thorsten.py \
    --revision "$THORSTEN_MODEL_REVISION" --local-dir "$MODEL_DIR" \
    --max-workers "$RUNPOD_TTS_DOWNLOAD_WORKERS"
fi
for artifact in llm.pt flow.pt spk2info.pt hift.pt infer_thorsten.py; do
  test -s "$MODEL_DIR/$artifact" || { echo "Required model file missing: $artifact" >&2; exit 1; }
done
for artifact in cosyvoice3.yaml campplus.onnx speech_tokenizer_v3.onnx speech_tokenizer_v3.batch.onnx; do
  test -s "$MODEL_DIR/$artifact" || { echo "Required base-model artifact missing: $artifact" >&2; exit 1; }
done
cp "$MODEL_DIR/infer_thorsten.py" "$MODEL_ROOT/infer_thorsten.py"
cmp -s "$MODEL_DIR/infer_thorsten.py" "$MODEL_ROOT/infer_thorsten.py" || {
  echo "Thorsten inference adapter copy failed integrity check." >&2
  exit 1
}
# Fail before declaring ready if the native runtime cannot even import.
# Actual GPU model loading remains a separate mandatory live acceptance step.
(
  cd "$MODEL_ROOT"
  PYTHONPATH="$MODEL_ROOT:$MODEL_ROOT/third_party/Matcha-TTS:${PYTHONPATH:-}" \
    "$VENV/bin/python" - <<'PY'
import sys
import torch
import torchaudio
from importlib.metadata import version
from cosyvoice.cli.cosyvoice import CosyVoice3
assert sys.version_info[:3] == (3, 11, 18), sys.version
assert torch.__version__.split("+", 1)[0] == "2.3.1", torch.__version__
assert torchaudio.__version__.split("+", 1)[0] == "2.3.1", torchaudio.__version__
assert version("huggingface-hub") == "0.36.0"
assert version("openai-whisper") == "20231117"
print("Thorsten native runtime import OK", torch.__version__)
PY
)
# A private weight checksum audit record, never committed to Git.
(
  cd "$MODEL_DIR"
  sha256sum llm.pt flow.pt spk2info.pt hift.pt infer_thorsten.py \
    cosyvoice3.yaml campplus.onnx speech_tokenizer_v3.onnx \
    speech_tokenizer_v3.batch.onnx > "$CHECKSUMS"
)
chmod 600 "$CHECKSUMS"
(cd "$MODEL_DIR" && sha256sum --check --status "$CHECKSUMS") || {
  echo "Downloaded Thorsten weights failed the local SHA-256 consistency check." >&2
  exit 1
}
# The marker is written only after every installation stage has passed.
printf '%s\n' "$VERSION" > "$MODEL_ROOT/.studio-ready"
echo "Thorsten engine installed in $MODEL_ROOT."
echo "RunPod preview and GPU listening validation are still mandatory."
