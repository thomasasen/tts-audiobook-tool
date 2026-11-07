#!/usr/bin/env bash
# Shared fail-closed checkout logic for the pinned CosyVoice/Matcha source.
# Source after env.sh; functions receive the persistent model checkout path.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/model-pins.env"

verify_cosyvoice_checkout() {
  local model_root="$1" origin head submodule_head status
  [[ -d "$model_root/.git" ]] || { echo "Pinned CosyVoice Git checkout is missing." >&2; return 1; }
  origin="$(git -C "$model_root" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    "$COSYVOICE_GIT_URL"|https://github.com/FunAudioLLM/CosyVoice.git) ;;
    *) echo "Unexpected CosyVoice origin: $origin" >&2; return 1 ;;
  esac
  head="$(git -C "$model_root" rev-parse HEAD 2>/dev/null || true)"
  [[ "$head" == "$COSYVOICE_REVISION" ]] || {
    echo "CosyVoice revision mismatch: expected $COSYVOICE_REVISION, got ${head:-missing}." >&2
    return 1
  }
  [[ -d "$model_root/third_party/Matcha-TTS" ]] || {
    echo "Pinned Matcha-TTS submodule is missing." >&2; return 1
  }
  submodule_head="$(git -C "$model_root/third_party/Matcha-TTS" rev-parse HEAD 2>/dev/null || true)"
  [[ "$submodule_head" == "$MATCHA_TTS_REVISION" ]] || {
    echo "Matcha-TTS submodule mismatch: expected $MATCHA_TTS_REVISION, got ${submodule_head:-missing}." >&2
    return 1
  }
  status="$(git -C "$model_root" status --porcelain --untracked-files=no)"
  [[ -z "$status" ]] || {
    echo "Pinned CosyVoice checkout has tracked modifications; refusing to trust it." >&2
    printf '%s\n' "$status" >&2
    return 1
  }
}

ensure_cosyvoice_checkout() {
  local model_root="$1" origin
  if [[ -d "$model_root/.git" ]]; then
    origin="$(git -C "$model_root" remote get-url origin 2>/dev/null || true)"
    case "$origin" in
      "$COSYVOICE_GIT_URL"|https://github.com/FunAudioLLM/CosyVoice.git) ;;
      *) echo "Unexpected CosyVoice origin: $origin" >&2; return 1 ;;
    esac
    if [[ -n "$(git -C "$model_root" status --porcelain --untracked-files=no)" ]]; then
      echo "CosyVoice checkout has tracked local changes. Refusing to overwrite it." >&2
      return 1
    fi
  else
    if [[ -e "$model_root" ]] && [[ -n "$(find "$model_root" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      echo "CosyVoice directory is non-empty but has no Git metadata. Refusing to overwrite." >&2
      return 1
    fi
    mkdir -p "$(dirname "$model_root")"
    git clone --no-checkout "$COSYVOICE_GIT_URL" "$model_root"
  fi
  git -C "$model_root" fetch --no-tags origin "$COSYVOICE_REVISION"
  git -C "$model_root" checkout --detach "$COSYVOICE_REVISION"
  git -C "$model_root" submodule sync --recursive
  git -C "$model_root" submodule update --init --recursive
  verify_cosyvoice_checkout "$model_root"
}
