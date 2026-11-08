#!/usr/bin/env bash
# Prominent end-of-boot report. The caller MUST route stdout to the Pod's
# original container stdout (fd 3), bypassing autostart's persistent tee log.
# This script reads a generated bearer token; never pipe its output to disk.
set -Eeuo pipefail

: "${RUNPOD_SUMMARY_WEB:=unknown}"
: "${RUNPOD_SUMMARY_THORSTEN:=not-ready}"
: "${RUNPOD_SUMMARY_CUDA_SYSTEM:=unknown}"
: "${RUNPOD_SUMMARY_CUDA_THORSTEN:=not-tested}"
: "${RUNPOD_SUMMARY_GPU:=unknown}"
: "${RUNPOD_SUMMARY_COMMIT:=unknown}"
: "${RUNPOD_SUMMARY_PORT:=7860}"
: "${RUNPOD_SUMMARY_DATA_ROOT:=/workspace/audiobook-data}"
: "${RUNPOD_SUMMARY_WORKSPACE:=/workspace}"
: "${RUNPOD_SUMMARY_TUI:=disabled}"

case "$RUNPOD_SUMMARY_WEB" in ready|failed) ;; *) echo "Invalid web status" >&2; exit 2 ;; esac
case "$RUNPOD_SUMMARY_THORSTEN" in installed|not-ready) ;; *) echo "Invalid voice status" >&2; exit 2 ;; esac
case "$RUNPOD_SUMMARY_CUDA_SYSTEM" in passed|failed|unknown) ;; *) echo "Invalid system CUDA status" >&2; exit 2 ;; esac
case "$RUNPOD_SUMMARY_CUDA_THORSTEN" in passed|failed|not-tested) ;; *) echo "Invalid Thorsten CUDA status" >&2; exit 2 ;; esac

line() { printf '  %-19s : %s\n' "$1" "$2"; }
rule() { printf '%s\n' '+====================================================================================================+'; }

if [[ "$RUNPOD_SUMMARY_WEB" != ready ]]; then
  status="START FEHLGESCHLAGEN: WEB-SERVER NICHT ERREICHBAR"
elif [[ "$RUNPOD_SUMMARY_CUDA_SYSTEM" == failed ]]; then
  status="START NICHT BETRIEBSBEREIT: CUDA-TEST FEHLGESCHLAGEN"
elif [[ "$RUNPOD_SUMMARY_THORSTEN" == installed && "$RUNPOD_SUMMARY_CUDA_THORSTEN" == passed ]]; then
  status="START ABGESCHLOSSEN: WEB + THORSTEN INSTALLIERT"
else
  status="WEB BEREIT: SPRECHER NOCH NICHT BETRIEBSBEREIT"
fi

# A failed model download or CUDA test must not be reported as successful TTS.
case "$RUNPOD_SUMMARY_THORSTEN:$RUNPOD_SUMMARY_CUDA_THORSTEN" in
  installed:passed) voice_status="Installiert; CUDA-Tensor-Test bestanden (Hoerprobe noch offen)" ;;
  installed:failed) voice_status="Installiert; CUDA-Test FEHLGESCHLAGEN" ;;
  *) voice_status="Noch nicht betriebsbereit; Web kann EPUBs vorbereiten" ;;
esac

free="nicht ermittelbar"
if command -v df >/dev/null 2>&1 && [[ -d "$RUNPOD_SUMMARY_WORKSPACE" ]]; then
  free="$(df -h --output=avail "$RUNPOD_SUMMARY_WORKSPACE" 2>/dev/null | tail -n 1 | xargs || true)"
  [[ -n "$free" ]] || free="nicht ermittelbar"
fi

printf '\n\n'
rule
printf '| %-98s |\n' 'GERMAN AUDIOBOOK STUDIO'
printf '| %-98s |\n' "$status"
rule
line "Weboberflaeche" "RunPod > Connect > HTTP $RUNPOD_SUMMARY_PORT"
line "Web-API" "$([[ "$RUNPOD_SUMMARY_WEB" == ready ]] && echo 'BEREIT' || echo 'NICHT ERREICHBAR') (Port $RUNPOD_SUMMARY_PORT)"
line "Deutsche Stimme" "$voice_status"
line "GPU" "$RUNPOD_SUMMARY_GPU"
line "CUDA (Basis)" "$RUNPOD_SUMMARY_CUDA_SYSTEM"
line "CUDA (Thorsten)" "$RUNPOD_SUMMARY_CUDA_THORSTEN"
line "Git-Version" "$RUNPOD_SUMMARY_COMMIT (master)"
line "Network Volume" "$RUNPOD_SUMMARY_WORKSPACE ($free frei)"
line "Daten/Projekte" "$RUNPOD_SUMMARY_DATA_ROOT"
line "Alte TUI" "$RUNPOD_SUMMARY_TUI"

if [[ -n "${AUDIOBOOK_STUDIO_TOKEN:-}" ]]; then
  line "Anmeldung" "Eigener AUDIOBOOK_STUDIO_TOKEN (Secret; wird nicht angezeigt)"
else
  token_file="$RUNPOD_SUMMARY_DATA_ROOT/studio-access-token"
  if [[ -f "$token_file" && ! -L "$token_file" && -r "$token_file" ]]; then
    token="$(<"$token_file")"
    if (( ${#token} >= 32 )) && [[ "$token" != *$'\n'* ]]; then
      line "ZUGANGSSCHLUESSEL" "$token"
    else
      line "Anmeldung" "Token-Datei ungueltig; bitte lokal pruefen"
    fi
    unset token
  else
    line "Anmeldung" "Token-Datei fehlt; Webserver-Logs pruefen"
  fi
fi

if [[ "$RUNPOD_SUMMARY_WEB" != ready ]]; then
  line "Diagnose" "tmux capture-pane -pt audiobook-web -S -100"
elif [[ "$RUNPOD_SUMMARY_CUDA_SYSTEM" == failed ]]; then
  line "AKTION ERFORDERLICH" "GPU-Pod STOPPEN; anderen RunPod-GPU-Host waehlen"
elif [[ "$RUNPOD_SUMMARY_THORSTEN" != installed || "$RUNPOD_SUMMARY_CUDA_THORSTEN" != passed ]]; then
  line "AKTION ERFORDERLICH" "Thorsten-Installationsstatus und private Autostart-Logs pruefen"
else
  line "Naechster Schritt" "Sprecher > Echte Hoerprobe erzeugen, anhoeren und freigeben"
fi
rule
printf '%s\n\n' 'SICHERHEIT: Zugangsschluessel nur in privaten Container-Logs. Nicht teilen.'
