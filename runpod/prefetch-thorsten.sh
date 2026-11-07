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

# The source checkout is created and pinned before any model bytes are written.
mkdir -p "$AUDIOBOOK_DATA_ROOT/models"
ensure_cosyvoice_checkout "$MODEL_ROOT"

# An isolated, pinned small CLI avoids waiting for the gigantic Thorsten pip
# environment. uv reuses the persistent cache and never changes Qwen/Thorsten.
# Hugging Face already downloads multiple files concurrently via --max-workers.
# Do not run base and fine-tune downloads simultaneously: both write llm.pt,
# flow.pt and spk2info.pt into the exact same target folder.
export HF_HUB_ENABLE_HF_TRANSFER=0
export HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-60}"
export HF_HUB_ETAG_TIMEOUT="${HF_HUB_ETAG_TIMEOUT:-20}"
hf_download() {
  "$UV" tool run --python 3.12 --from "huggingface_hub==0.36.0" \
    hf download "$@" --max-workers "$RUNPOD_TTS_DOWNLOAD_WORKERS"
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

echo "Prefetching CosyVoice3 base model (max $RUNPOD_TTS_DOWNLOAD_WORKERS parallel files)."
hf_download "$BASE_MODEL_ID" --revision "$BASE_MODEL_REVISION" --local-dir "$MODEL_DIR"
echo "Overlaying Thorsten fine-tuning AFTER the base download completes."
hf_download "$THORSTEN_MODEL_ID" llm.pt flow.pt spk2info.pt infer_thorsten.py \
  --revision "$THORSTEN_MODEL_REVISION" --local-dir "$MODEL_DIR"

# The base and fine-tune snapshots overlap filenames. Capture the FINAL
# overlaid bytes to prevent the installer from re-downloading base and
# Thorsten weights, which otherwise doubles disk/network writes.
for name in llm.pt flow.pt spk2info.pt hift.pt infer_thorsten.py cosyvoice3.yaml campplus.onnx speech_tokenizer_v3.onnx speech_tokenizer_v3.batch.onnx; do
  [[ -s "$MODEL_DIR/$name" ]] || { echo "Missing prefetched file: $name" >&2; exit 1; }
done
(cd "$MODEL_DIR" && sha256sum llm.pt flow.pt spk2info.pt hift.pt infer_thorsten.py) > "$PREFETCH_SUMS.tmp"
mv -f "$PREFETCH_SUMS.tmp" "$PREFETCH_SUMS"
printf '%s\n' "$PREFETCH_VERSION" > "$PREFETCH_READY.tmp"
mv -f "$PREFETCH_READY.tmp" "$PREFETCH_READY"
echo "Thorsten weights prefetched and verified; final installer still required."
