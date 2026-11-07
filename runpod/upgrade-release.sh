#!/usr/bin/env bash
set -Eeuo pipefail

RUNPOD_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$RUNPOD_SCRIPT_DIR/.." && pwd)"
source "$RUNPOD_SCRIPT_DIR/env.sh"
source "$RUNPOD_SCRIPT_DIR/model-pins.env"

if [[ "${1:-}" != "--confirm" || $# -ne 1 ]]; then
  echo "Usage: bash runpod/upgrade-release.sh --confirm" >&2
  echo "This backs up the active persistent release, installs the pinned release, and restores the old runtime automatically on failure." >&2
  exit 2
fi

mkdir -p "$AUDIOBOOK_DATA_ROOT/locks"
exec 9>"$AUDIOBOOK_DATA_ROOT/locks/release-upgrade.lock"
flock -n 9 || { echo "Another release upgrade or rollback is already running." >&2; exit 1; }

REPO_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null || printf 'nogit')"
BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)-$REPO_COMMIT"
BACKUP_DIR="$AUDIOBOOK_DATA_ROOT/releases/tts-audiobook-tool/$BACKUP_ID"
BACKUP_CREATED=0
UPGRADE_SUCCEEDED=0

python3 "$RUNPOD_SCRIPT_DIR/release_transaction.py" backup \
  --data-root "$AUDIOBOOK_DATA_ROOT" \
  --venv-root "$AUDIOBOOK_VENV_DIR" \
  --repo-root "$REPO_ROOT" \
  --backup-id "$BACKUP_ID"
BACKUP_CREATED=1

OPTIONAL_MODELS=none
HAS_QWEN=0
HAS_MOSS=0
[[ -d "$BACKUP_DIR/assets/venvs/qwen" ]] && HAS_QWEN=1
[[ -d "$BACKUP_DIR/assets/venvs/moss" ]] && HAS_MOSS=1
if [[ "$HAS_QWEN" -eq 1 && "$HAS_MOSS" -eq 1 ]]; then
  OPTIONAL_MODELS=both
elif [[ "$HAS_QWEN" -eq 1 ]]; then
  OPTIONAL_MODELS=qwen
elif [[ "$HAS_MOSS" -eq 1 ]]; then
  OPTIONAL_MODELS=moss
fi

restore_previous_release() {
  local status=$?
  if [[ "$UPGRADE_SUCCEEDED" -ne 1 && "$BACKUP_CREATED" -eq 1 ]]; then
    echo "Release upgrade failed. Restoring the previous persistent runtime." >&2
    if ! python3 "$RUNPOD_SCRIPT_DIR/release_transaction.py" restore \
      --data-root "$AUDIOBOOK_DATA_ROOT" \
      --venv-root "$AUDIOBOOK_VENV_DIR" \
      --repo-root "$REPO_ROOT" \
      --backup-id "$BACKUP_ID" \
      --confirm --allow-code-mismatch; then
      echo "CRITICAL: automatic rollback failed. Preserve $BACKUP_DIR and inspect release_transaction.py output." >&2
      return 1
    fi
    echo "Previous runtime assets restored. Previous application code commit: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("repo_commit") or "unknown")' "$BACKUP_DIR/metadata.json")." >&2
  fi
  return "$status"
}
trap restore_previous_release EXIT

echo "Installing pinned RunPod release. Previous release backup: $BACKUP_ID"
echo "Preserving optional TUI model environments: $OPTIONAL_MODELS"
RUNPOD_TTS_MODELS="$OPTIONAL_MODELS" bash "$RUNPOD_SCRIPT_DIR/setup.sh"
bash "$RUNPOD_SCRIPT_DIR/prefetch-thorsten.sh"
bash "$RUNPOD_SCRIPT_DIR/install-thorsten.sh"
bash "$RUNPOD_SCRIPT_DIR/install-web.sh"
bash "$RUNPOD_SCRIPT_DIR/build-web.sh"

UPGRADE_SUCCEEDED=1
trap - EXIT
echo "RunPod release upgrade completed."
echo "Rollback backup retained at: $BACKUP_DIR"
echo "The previous application code commit is recorded in $BACKUP_DIR/metadata.json."
