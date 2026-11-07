#!/usr/bin/env bash
# Curated native German Thorsten CosyVoice3 model, isolated from Qwen/MOSS venvs.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"
MODEL_ROOT="$AUDIOBOOK_DATA_ROOT/models/CosyVoice"
MODEL_DIR="$MODEL_ROOT/pretrained_models/CosyVoice3-0.5B"
VENV="$AUDIOBOOK_VENV_DIR/thorsten"
UV="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap/bin/uv"
VERSION="CosyVoice-ace7c47-Thorsten-CosyVoice3-2026-11-v1"
CHECKSUMS="$AUDIOBOOK_DATA_ROOT/models/thorsten-model-sha256.txt"
mkdir -p "$AUDIOBOOK_DATA_ROOT/models" "$AUDIOBOOK_VENV_DIR"
if [[ ! -x "$UV" ]]; then
  echo "Run bash runpod/setup.sh --models qwen before installing Thorsten." >&2
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
      -s "$MODEL_DIR/hift.pt" && -s "$MODEL_ROOT/infer_thorsten.py" &&
      -s "$CHECKSUMS" ]] &&
   (cd "$MODEL_DIR" && sha256sum --check --status "$CHECKSUMS"); then
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
"$UV" python install 3.11
if [[ ! -x "$VENV/bin/python" ]]; then
  "$UV" venv --python 3.11 --seed "$VENV"
fi
if [[ ! -d "$MODEL_ROOT/.git" ]]; then
  git clone https://github.com/FunAudioLLM/CosyVoice.git "$MODEL_ROOT"
fi
if [[ -n "$(git -C "$MODEL_ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo "CosyVoice checkout has local changes. Refusing to overwrite it." >&2
  exit 1
fi
git -C "$MODEL_ROOT" checkout --detach ace7c47
git -C "$MODEL_ROOT" submodule update --init --recursive
if command -v apt-get >/dev/null 2>&1 && [[ "$EUID" -eq 0 ]]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sox libsox-fmt-all
fi
# Do not upgrade setuptools: v82+ removes pkg_resources needed by Lightning.
"$VENV/bin/python" -m pip install --disable-pip-version-check --no-input "setuptools==80.9.0"
"$VENV/bin/python" -m pip install --disable-pip-version-check openai-whisper huggingface_hub
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
  "$VENV/bin/hf" download FunAudioLLM/Fun-CosyVoice3-0.5B-2512 --local-dir "$MODEL_DIR" \
    --max-workers "$RUNPOD_TTS_DOWNLOAD_WORKERS"
  "$VENV/bin/hf" download Thorsten-Voice/CosyVoice3 llm.pt flow.pt spk2info.pt infer_thorsten.py \
    --local-dir "$MODEL_DIR" --max-workers "$RUNPOD_TTS_DOWNLOAD_WORKERS"
fi
cp "$MODEL_DIR/infer_thorsten.py" "$MODEL_ROOT/infer_thorsten.py"
for artifact in llm.pt flow.pt spk2info.pt hift.pt; do
  test -s "$MODEL_DIR/$artifact" || { echo "Required model file missing: $artifact" >&2; exit 1; }
done
# Fail before declaring ready if the native runtime cannot even import.
# Actual GPU model loading remains a separate mandatory live acceptance step.
(
  cd "$MODEL_ROOT"
  PYTHONPATH="$MODEL_ROOT:$MODEL_ROOT/third_party/Matcha-TTS:${PYTHONPATH:-}" \
    "$VENV/bin/python" - <<'PY'
import torch
import torchaudio
from cosyvoice.cli.cosyvoice import CosyVoice3
print("Thorsten native runtime import OK", torch.__version__)
PY
)
# A private weight checksum audit record, never committed to Git.
(
  cd "$MODEL_DIR"
  sha256sum llm.pt flow.pt spk2info.pt hift.pt \
    > "$CHECKSUMS"
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
