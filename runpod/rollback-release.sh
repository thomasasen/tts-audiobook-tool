#!/usr/bin/env bash
set -Eeuo pipefail

RUNPOD_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$RUNPOD_SCRIPT_DIR/.." && pwd)"
source "$RUNPOD_SCRIPT_DIR/env.sh"

if [[ $# -lt 2 ]]; then
  echo "Usage: bash runpod/rollback-release.sh <backup-id|latest> --confirm [--allow-code-mismatch]" >&2
  exit 2
fi

BACKUP_ID="$1"
shift
CONFIRM=0
ALLOW_CODE_MISMATCH=0
for argument in "$@"; do
  case "$argument" in
    --confirm) CONFIRM=1 ;;
    --allow-code-mismatch) ALLOW_CODE_MISMATCH=1 ;;
    *) echo "Unknown argument: $argument" >&2; exit 2 ;;
  esac
done
if [[ "$CONFIRM" -ne 1 ]]; then
  echo "Rollback is destructive and requires --confirm." >&2
  exit 2
fi

mkdir -p "$AUDIOBOOK_DATA_ROOT/locks"
exec 9>"$AUDIOBOOK_DATA_ROOT/locks/release-upgrade.lock"
flock -n 9 || { echo "Another release upgrade or rollback is already running." >&2; exit 1; }

BACKUP_DIR="$AUDIOBOOK_DATA_ROOT/releases/tts-audiobook-tool/$BACKUP_ID"
if [[ "$BACKUP_ID" == "latest" ]]; then
  BACKUP_DIR="$(python3 "$RUNPOD_SCRIPT_DIR/release_transaction.py" list --data-root "$AUDIOBOOK_DATA_ROOT" | python3 -c 'import json,sys; items=[x for x in json.load(sys.stdin) if x.get("state")=="available"]; print(max(items, key=lambda x:x.get("created_at") or "")["path"] if items else "")')"
fi
if [[ -z "$BACKUP_DIR" || ! -f "$BACKUP_DIR/metadata.json" ]]; then
  echo "Requested release backup does not exist or is incomplete: $BACKUP_ID" >&2
  exit 1
fi
PREVIOUS_CODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("repo_commit") or "unknown")' "$BACKUP_DIR/metadata.json")"

ARGS=(
  restore
  --data-root "$AUDIOBOOK_DATA_ROOT"
  --venv-root "$AUDIOBOOK_VENV_DIR"
  --repo-root "$REPO_ROOT"
  --backup-id "$BACKUP_ID"
  --confirm
)
if [[ "$ALLOW_CODE_MISMATCH" -eq 1 ]]; then
  ARGS+=(--allow-code-mismatch)
fi
python3 "$RUNPOD_SCRIPT_DIR/release_transaction.py" "${ARGS[@]}"

echo "Persistent runtime assets restored from backup: $BACKUP_ID"
echo "Application code commit recorded by that release: $PREVIOUS_CODE"
if [[ "$PREVIOUS_CODE" != "unknown" ]]; then
  echo "If the application code must also be rolled back, review and then run: git -C \"$REPO_ROOT\" checkout $PREVIOUS_CODE"
fi
