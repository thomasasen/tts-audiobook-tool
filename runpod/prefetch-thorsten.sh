#!/usr/bin/env bash
# Pre-fetch official and Thorsten fine-tuned weights while Qwen is installing.
# This stage NEVER creates .studio-ready or a checksum manifest: only the
# subsequent install-thorsten.sh may validate and trust a completed model.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/env.sh"
source "$SCRIPT_DIR/model-pins.env"
source "$SCRIPT_DIR/model-source.sh"

MODEL_ROOT="$AUDIOBOOK_DATA_ROOT/models/CosyVoice"
MODEL_DIR="$MODEL_ROOT/pretrained_models/CosyVoice3-0.5B"
UV="$AUDIOBOOK_DATA_ROOT/tools/uv-bootstrap/bin/uv"

: "${RUNPOD_TTS_DOWNLOAD_WORKERS:=4}"
if [[ ! "$RUNPOD_TTS_DOWNLOAD_WORKERS" =~ ^[1-8]$ ]]; then
  echo "RUNPOD_TTS_DOWNLOAD_WORKERS must be 1..8." >&2
  exit 2
fi

if [[ -f "$MODEL_ROOT/.studio-ready" ]]; then
  echo "Thorsten has an existing ready marker; the installer will verify it."
  verify_cosyvoice_checkout "$MODEL_ROOT"
  exit 0
fi
if [[ ! -x "$UV" ]]; then
  echo "uv is missing. Run runpod/setup.sh --bootstrap-only first." >&2
  exit 1
fi
actual_uv_version="$("$UV" --version | awk '{print $2}')"
if [[ "$actual_uv_version" != "$UV_VERSION" ]]; then
  echo "uv version mismatch: expected $UV_VERSION, got $actual_uv_version." >&2
  exit 1
fi

# The source checkout is created and pinned before any model bytes are written.
mkdir -p "$AUDIOBOOK_DATA_ROOT/models"
ensure_cosyvoice_checkout "$MODEL_ROOT"

# Use the same pinned Hugging Face client for minimal downloads and upstream
# LFS/Git-blob verification. Only required base assets and Thorsten overlays are fetched.
export HF_HUB_ENABLE_HF_TRANSFER=0
export HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-60}"
export HF_HUB_ETAG_TIMEOUT="${HF_HUB_ETAG_TIMEOUT:-20}"
run_hf_script() {
  local script="$1"
  shift
  "$UV" tool run --python "$WEB_PYTHON_VERSION" --with "huggingface-hub==${HF_HUB_VERSION}" \
    python "$SCRIPT_DIR/$script" "$@"
}

mkdir -p "$MODEL_DIR"
PREFETCH_VERSION="$MODEL_RELEASE_ID"
PREFETCH_SUMS="$MODEL_ROOT/.prefetch-sha256.txt"
PREFETCH_READY="$MODEL_ROOT/.prefetch-complete"
if [[ -f "$PREFETCH_READY" &&
      "$(cat "$PREFETCH_READY")" == "$PREFETCH_VERSION" &&
      -s "$PREFETCH_SUMS" ]] &&
   (cd "$MODEL_DIR" && sha256sum --check --status "$PREFETCH_SUMS"); then
  echo "Previously prefetched model files pass SHA-256 integrity; no download."
  exit 0
fi
# A previous marker with invalid checksums is a genuine integrity problem,
# not permission to silently rebaseline potentially corrupted weights.
if [[ -e "$PREFETCH_READY" ]]; then
  echo "Existing Thorsten prefetch integrity/version failed. Refusing silent rebaseline." >&2
  exit 1
fi
if [[ -d "$MODEL_DIR" ]] && find "$MODEL_DIR" -mindepth 1 -maxdepth 1 -type f -print -quit | grep -q .; then
  echo "Unmarked model files already exist without a verified prefetch marker." >&2
  echo "Refusing to overwrite or silently rebaseline partial/corrupt model data." >&2
  exit 1
fi

echo "Prefetching the pinned minimal CosyVoice3 and Thorsten asset set."
run_hf_script download-hf-models.py \
  --model-dir "$MODEL_DIR" \
  --base-model-id "$BASE_MODEL_ID" --base-revision "$BASE_MODEL_REVISION" \
  --thorsten-model-id "$THORSTEN_MODEL_ID" --thorsten-revision "$THORSTEN_MODEL_REVISION" \
  --max-workers "$RUNPOD_TTS_DOWNLOAD_WORKERS"
run_hf_script verify-hf-models.py \
  --model-dir "$MODEL_DIR" \
  --base-model-id "$BASE_MODEL_ID" --base-revision "$BASE_MODEL_REVISION" \
  --thorsten-model-id "$THORSTEN_MODEL_ID" --thorsten-revision "$THORSTEN_MODEL_REVISION" \
  --output "$PREFETCH_SUMS"
(cd "$MODEL_DIR" && sha256sum --check "$PREFETCH_SUMS")
printf '%s\n' "$PREFETCH_VERSION" > "$PREFETCH_READY.tmp"
mv -f "$PREFETCH_READY.tmp" "$PREFETCH_READY"
echo "Thorsten weights prefetched and verified; final installer still required."
