# RunPod: German Audiobook Studio (integrierter Entwicklungsbranch)

**Status:** Der neue Release-Validierungsbranch ergänzt EPUB-/Browser-/Recovery-Prüfungen. **GPU und echte deutsche Sprachausgabe sind weiterhin nicht abgenommen**. Der vollständige kopierbare Docker-Startbefehl steht ganz oben in der [README](../README.md). Diese Anleitung beschreibt den Betrieb und Fehlerfälle.

## Architektur

- `/workspace/tts-audiobook-tool`: der geprüfte Branch `feature/german-audiobook-release-hardening`.
- `/workspace/audiobook-data`: ausschließlich persistente Daten und Modelle, niemals Git-Artefakte.
- `/workspace/audiobook-data/venvs/qwen`: Python 3.12, bestehende Projektlaufzeit und TUI.
- `/workspace/audiobook-data/venvs/thorsten`: isolierte Python-3.11-CosyVoice3-Umgebung.
- `/workspace/audiobook-data/models/CosyVoice`: offizielles FunAudioLLM/CosyVoice, Commit `ace7c47`, Thorsten-Fine-Tuning, `spk2info.pt`.
- `tmux attach -t audiobook`: vorhandene TUI (Qwen; MOSS optional mit `RUNPOD_TTS_MODELS=both`).
- `tmux attach -t audiobook-web`: FastAPI und die bereits vorhandene React-Weboberfläche, Port `7860`.

## Setup / Dienste

```bash
# Nur nachdem das Repository auf dem Volume ist:
bash /workspace/tts-audiobook-tool/runpod/setup.sh --models qwen
bash /workspace/tts-audiobook-tool/runpod/install-thorsten.sh
bash /workspace/tts-audiobook-tool/runpod/start-web.sh
```

Der vollständige Pod-Autostart übernimmt alle Schritte und bewahrt daneben den ursprünglichen /start.sh-Dienst. **Kein Modell wird beim erneuten Start heruntergeladen**, wenn die korrekte persistente Installation bereits erkannt wird. Ein beschädigter Download erfordert eine erneute Installation.

## Zugang / Betrieb

Nach dem ersten Start über den privaten RunPod-Terminalzugang den Token holen:

```bash
cat /workspace/audiobook-data/studio-access-token
```

Alternativ im RunPod-Pod vor dem Start ein privates `AUDIOBOOK_STUDIO_TOKEN` mit mindestens 32 Zeichen als Secret konfigurieren. Die Webanwendung ist unter der RunPod-HTTPS-Proxyadresse des HTTP-Ports **7860** erreichbar. Die App schützt sämtliche privaten API-Endpunkte über Bearer-Token; Netzwerkzugang zusätzlich einschränken. Keine öffentlichen EPUBs, Tokens oder Hörbücher in Git.

Das Sprachmodell ist bewusst als *bereit* gesperrt, bis ein Admin eine echte Hörprobe generiert, abgehört und freigegeben hat. Sollte der Thorsten-Installer scheitern, läuft die geschützte Kapitelvorbereitung ohne TTS weiter. Es gibt keine generische amerikanische Ersatzstimme.

## Betriebshinweise

- `nvidia-smi`: CUDA/GPU prüfen.
- `bash /workspace/tts-audiobook-tool/runpod/setup.sh --check-only`: Systemdiagnose.
- `ls -lt /workspace/audiobook-data/logs`: Boot- und Modellprotokolle (privat).
- `tmux ls`: laufende Dienste prüfen; `tmux attach -t audiobook-web`: Web-Logs.
- `/workspace/audiobook-data/studio-projects`: importierte Bücher im originalen Project-V5-Schema.
- `/workspace/audiobook-data/studio-jobs`: persistente Aufträge, WAV-Segmente, M4B-Exporte.
- `/workspace/audiobook-data/studio.sqlite3`: Projekt- und Auftragsmetadaten.
- `/workspace/audiobook-data/models`: installierte offene Modellgewichte.
- `/workspace/audiobook-data/cache`: Hugging-Face- und Python-Caches.

**Anmerkung zur Sicherheit:** Das Quell-Repository muss vor dem Deployment vertraut und geprüft sein. Für einen reproduzierbaren Release später Commit-SHA statt Branch-HEAD pinnen und Modellgewichte ebenfalls auf geprüfte Revisionen fixieren. Hier werden Entwicklungsbranches verwendet.

## Qualitätssicherung und bekannte Grenzen

Die GitHub-Actions-Prüfungen validieren Bash-Syntax, Installation-/Autostart-Verträge und den Python-/React-Build, nicht die GPU-Funktion. Ausstehend sind insbesondere ein echter Modell-/Lizenz-/Hörvergleich (Thorsten, Qwen, MOSS), ein vollständiger EPUB-zu-M4B-Ablauf auf einem Pod, Prozessneustart und Qualitätssicherung durch ASR. Die notwendigen Freigabepunkte stehen in [docs/studio-production-progress.md](../docs/studio-production-progress.md).

PR-Abhängigkeiten: #8 → #9 → #10 → #11 → #12. Der unabhängige PR #6 ist Quelle der konservativ übernommenen RunPod-Skripte und muss vor dem finalen Merge gegen die integrierte Variante abgeglichen werden.
