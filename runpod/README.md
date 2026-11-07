# RunPod: German Audiobook Studio (automatischer Webbetrieb)

**Status:** Der für `master` freigegebene Integrationsstand aus PR #14 ergänzt EPUB-/Browser-/Recovery-Prüfungen. **GPU und echte deutsche Sprachausgabe sind weiterhin nicht abgenommen.** Die maßgebliche RunPod-Template-Konfiguration, die aktuelle Speicherempfehlung (15 GB Container Disk und 60 GB Network Volume) und der kopierbare Docker-Startbefehl stehen **ausschließlich am Anfang der [Haupt-README](../README.md)**. Diese Datei beschreibt die technische Umsetzung und Fehlerfälle; die originale TUI hat davon getrennte Funktionen und kann Referenzstimmen nutzen.

## Architektur

- `/workspace/tts-audiobook-tool`: Standardbranch `master` (RunPod-Test, keine Produktionsfreigabe).
- `/workspace/audiobook-data`: ausschließlich persistente Daten und Modelle, niemals Git-Artefakte.
- `/workspace/audiobook-data/venvs/qwen`: Python 3.12, bestehende Projektlaufzeit und TUI.
- `/workspace/audiobook-data/venvs/thorsten`: isolierte Python-3.11-CosyVoice3-Umgebung.
- `/workspace/audiobook-data/models/CosyVoice`: offizielles FunAudioLLM/CosyVoice, Commit `ace7c47`, Thorsten-Fine-Tuning, `spk2info.pt`.
- Die alte TUI wird **nicht automatisch gestartet** (`RUNPOD_TTS_START_TUI=0`, Standard). Nur wenn ausdrücklich `RUNPOD_TTS_START_TUI=1` gesetzt wird, läuft zusätzlich `tmux attach -t audiobook`; ein bestehender TUI-Prozess auf einem laufenden Pod wird nicht ungefragt beendet.
- `tmux attach -t audiobook-web`: FastAPI und die bereits vorhandene React-Weboberfläche, Port `7860`.
- Im Thorsten-Worker wird **nur** der für Referenzaudio bestimmte ONNX-Tokenizer `speech_tokenizer_v3.onnx` auf CPU initialisiert. PyTorch und die eigentliche Thorsten-SFT-Sprachsynthese verwenden weiterhin CUDA. Das vermeidet den bei der RTX 5000 Ada beobachteten ONNX-CUDA-Initialisierungsfehler 46, sofern CUDA für PyTorch verfügbar bleibt.

## Setup / Dienste

```bash
# Nur nachdem das Repository auf dem Volume ist:
bash /workspace/tts-audiobook-tool/runpod/setup.sh --models qwen
bash /workspace/tts-audiobook-tool/runpod/install-thorsten.sh
bash /workspace/tts-audiobook-tool/runpod/start-web.sh
```

Der vollständige Pod-Autostart übernimmt alle Schritte und bewahrt daneben den ursprünglichen /start.sh-Dienst. **Kein Modell wird beim erneuten Start heruntergeladen**, wenn die korrekte persistente Installation bereits erkannt wird. Ein beschädigter Download erfordert eine erneute Installation.

## Zugang / Betrieb

**Standard (ohne selbst vergebenes Secret):** Nach einem erfolgreichen Web-Healthcheck zeigt der RunPod-Autostart den automatisch erzeugten Schlüssel in den **privaten RunPod-Container-Logs** unter `GERMAN AUDIOBOOK STUDIO: ZUGANGSSCHLÜSSEL` an. Die Ausgabe erfolgt direkt auf Container-stdout, **nicht** in der persistenten Datei `/workspace/audiobook-data/logs/autostart-*.log`. RunPod selbst kann Container-Logs aufbewahren. **Logzugang und Screenshots vertraulich behandeln.**

Alternativ lässt sich der generierte Schlüssel weiterhin über den privaten RunPod-Terminalzugang auslesen:

```bash
cat /workspace/audiobook-data/studio-access-token
```

Wenn du vor dem Start einen eigenen `AUDIOBOOK_STUDIO_TOKEN` mit mindestens 32 Zeichen als Secret konfigurierst, wird **dieser Secret-Wert nicht in den Logs angezeigt**. Die Webanwendung ist unter der RunPod-HTTPS-Proxyadresse des HTTP-Ports **7860** erreichbar. Die App schützt sämtliche privaten API-Endpunkte über Bearer-Token; Netzwerkzugang zusätzlich einschränken. Keine öffentlichen EPUBs, Tokens oder Hörbücher in Git.

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

**Anmerkung zur Sicherheit:** Das Quell-Repository muss vor dem Deployment vertraut und geprüft sein. Für einen reproduzierbaren Release später Commit-SHA statt Branch-HEAD pinnen und Modellgewichte ebenfalls auf geprüfte Revisionen fixieren. Der automatische Teststart verwendet derzeit `master`, kein unveränderliches Produktionsimage.

## Qualitätssicherung und bekannte Grenzen

Die GitHub-Actions-Prüfungen validieren Bash-Syntax, Installation-/Autostart-Verträge und den Python-/React-Build, nicht die GPU-Funktion. Ausstehend sind insbesondere ein echter Modell-/Lizenz-/Hörvergleich (Thorsten, Qwen, MOSS), ein vollständiger EPUB-zu-M4B-Ablauf auf einem Pod, Prozessneustart und Qualitätssicherung durch ASR. Die notwendigen Freigabepunkte stehen in [docs/studio-production-progress.md](../docs/studio-production-progress.md).

Die ehemals gestapelten PRs #8–#14 wurden mit PR #14 nach `master` integriert. Die dokumentierten offenen GPU-/Hörtests bleiben Voraussetzung einer Produktionsfreigabe. Der separate ältere PR #6 wird nicht ungeprüft nachgemergt.
