# RunPod: German Audiobook Studio

Die **aktuelle, einzige Startvorlage einschließlich Docker Start Command** steht am Anfang der [Haupt-README](../README.md). Dieser Abschnitt dokumentiert die Architektur und Diagnose. Die früheren `feature/runpod-p0`-Startbefehle sind veraltet und dürfen nicht mehr im RunPod-Template verwendet werden.

## Standardbetrieb

- **Quelle:** `master` im GitHub-Repository, Checkout `/workspace/tts-audiobook-tool`.
- **Network Volume:** persistent auf `/workspace`; Daten unter `/workspace/audiobook-data`.
- **Webserver:** eigener Python-3.12-CPU-Runtime in `venvs/web`; **Qwen/MOSS sind nicht erforderlich**.
- **Deutsche Stimme:** Thorsten-CosyVoice3, eigener Python-3.11-Runtime in `venvs/thorsten`, GPU-Inferenz nach echter Hörprobe und Freigabe.
- **Weboberfläche:** React/Vite-Build aus `web`, FastAPI/Uvicorn auf `7860/http`, nur ein Web-Prozess pro Datenroot.
- **Legacy-TUI:** standardmäßig aus. Bei Bedarf `RUNPOD_TTS_MODELS=qwen` und `RUNPOD_TTS_START_TUI=1` bzw. MOSS explizit setzen.

## Optimierter Erststart

`runpod/autostart.sh` übernimmt den gesamten Ablauf:

1. Mount und Git-Stand prüfen, `runpod/setup.sh --models none --bootstrap-only` ausführen.
2. **Parallel** `runpod/build-web.sh` (npm) und, wenn CUDA und Speicherplatz ausreichen, `runpod/prefetch-thorsten.sh` (mehrere parallele Dateien mit maximal vier Workern) starten; `runpod/install-web.sh` installiert gleichzeitig die eigenständige Web-Laufzeit. Kein Qwen-/MOSS-Pflichtdownload.
3. Nach erfolgreichem Web-Runtime-Importcheck und React-Build `runpod/start-web.sh` in `tmux` starten; HTTP-Healthcheck auf `127.0.0.1:7860/api/health` muss erfolgreich sein. Ein Fehler im Webstart beendet den Autostart mit einem echten Fehlerstatus.
4. Den parallel vorbereiteten Download vollständig abschließen; `runpod/install-thorsten.sh` prüft die zwischengespeicherten Dateien, installiert CosyVoice-Abhängigkeiten, schreibt erst nach Erfolg die vertrauenswürdige `.studio-ready`-Markierung. Das Basis- und Finetuning-Modell werden **nacheinander** geladen, da sie gleichnamige Gewichtsdateien enthalten. Bereits erfolgreich vorab heruntergeladene Dateien werden mittels SHA-256 wiederverwendet.
5. Echte CUDA-Allokation in Thorstens Python-Umgebung prüfen. Der Webserver bleibt verfügbar, falls die Stimmeninstallation oder GPU-Inferenz scheitert.

Die Stufen sind bewusst begrenzt: maximal ein Frontend-Build, ein Web-Python-Installer und ein paralleler Thorsten-Vorabdownload. Ein neuer Thorsten-Download beginnt nur bei funktionierendem System-CUDA-Test und **mindestens 25 GiB** freiem Network-Volume-Speicher. Zur GPU-Initialisierung reicht `nvidia-smi` alleine nicht aus.

## Abschließende Statusanzeige

Nach den **gesamten** Installations- und Prüfschritten kontrolliert der Autostart nochmals die HTTP-Erreichbarkeit des Web-Studios. Anschließend ruft er `runpod/print-start-summary.sh` auf und schreibt eine große, eingerahmte Übersicht **ans Ende der privaten RunPod-Container-Logs**. Darin stehen:

- Weboberfläche (`RunPod > Connect > HTTP 7860`) und der überprüfte HTTP-Status.
- Thorsten-Installationsstatus, separater echter CUDA-Tensortest im Thorsten-Runtime und der weiterhin ausstehende Hörproben-/Hörqualitätsnachweis.
- Verwendete GPU, System-CUDA-Test, Git-Commit und Network-Volume-Pfad mit freiem Speicher.
- Automatisch generierter Web-Zugangsschlüssel oder bei eigenem `AUDIOBOOK_STUDIO_TOKEN` der Hinweis, dass dieses Secret nicht angezeigt wird.
- Nächster Bedienungsschritt im Studio.

Ein fehlgeschlagener Webcheck erhält eine **Fehlermeldung statt eines Erfolgsbanners**. Eine nicht installierte Stimme oder nicht betriebsbereites CUDA wird ausdrücklich als Warnung angezeigt.

**Datenschutz:** Die vollständige Abschlussübersicht wird über den beim Start gesicherten Dateideskriptor `3` auf das ursprüngliche Container-stdout ausgegeben; sie wird **nicht** durch `tee` in `autostart-*.log` gespeichert. Der kurz gefasste, nicht vertrauliche Status wird hingegen ins normale Autostart-Log geschrieben. Bitte die privaten Container-Logs nicht öffentlich teilen, weil sie den Zugangsschlüssel enthalten können.

## Verzeichnisse und Dienste

| Pfad oder Prozess | Inhalt |
| --- | --- |
| `/workspace/tts-audiobook-tool` | Quellcode und Frontend-Build |
| `/workspace/audiobook-data/venvs/web` | Web-Backend mit CPU-PyTorch |
| `/workspace/audiobook-data/venvs/thorsten` | native deutsche GPU-TTS-Umgebung |
| `/workspace/audiobook-data/venvs/qwen` | nur bei optionaler TUI-Installation |
| `/workspace/audiobook-data/models/CosyVoice` | CosyVoice-Checkout und Thorsten-Finetuning |
| `/workspace/audiobook-data/cache` | Pip-, uv-, Hugging-Face-, npm-Caches |
| `/workspace/audiobook-data/logs` | Autostart, Frontend-Build, Modellvorabdownload |
| `/workspace/audiobook-data/studio-projects` | importierte private EPUB-Projekte |
| `/workspace/audiobook-data/studio-jobs` | Segmente und M4B-Exporte |
| `/workspace/audiobook-data/studio.sqlite3` | Projekt-/Auftragszustand |
| `tmux: audiobook-web` | FastAPI und React-Weboberfläche, Port 7860 |
| `tmux: audiobook` | optionale ältere Konsolenanwendung |

## Prüfungen

```bash
# Echte CUDA-Speicherzuweisung statt bloßer GPU-Sichtbarkeit
python3 /workspace/tts-audiobook-tool/runpod/gpu_preflight.py --env system
python3 /workspace/tts-audiobook-tool/runpod/gpu_preflight.py --env thorsten

# Web-Gesundheit und Protokolle
curl -fsS http://127.0.0.1:7860/api/health
tmux capture-pane -pt audiobook-web -S -100 | tail -100
ls -lt /workspace/audiobook-data/logs | head
```

Die Web-Python-Umgebung lässt sich bei Problemen gezielt erneut prüfen:

```bash
bash /workspace/tts-audiobook-tool/runpod/install-web.sh
```

Keines dieser Prüfprogramme benötigt private Sprecheraufnahmen oder zusätzliche Qwen-/MOSS-Modellgewichte. Die Installation erzeugt einen Zugangsschlüssel, sofern kein eigenes privates `AUDIOBOOK_STUDIO_TOKEN` gesetzt ist. Der automatisch erzeugte Schlüssel wird nach dem frühen Webstart und **erneut in der abschließenden Statusübersicht** nur in den privaten Container-Logs ausgegeben, nie im persistenten Autostart-Protokoll. Keine Log-Screenshots mit sichtbaren Schlüsseln teilen.

## Bekannte Grenzen

Die CI prüft Bash-Startverträge, eine **tatsächlich isolierte Web-Python-Installation mit HTTP-Healthcheck**, den React-Build sowie Web-API- und Browser-Tests. Modellgewichtsdateien werden in diesen CI-Läufen nicht auf der GPU geladen. Eine reale deutsche Thorsten-Hörprobe, deren Qualität und die vollständige EPUB-zu-M4B-Ausgabe müssen noch auf einem geeigneten RunPod-Host abgenommen werden. Speicherbedarf und Erststart-Dauer sind abhängig von vorhandenen Caches, Network Volume, Datacenter und Verbindung.

Beim beobachteten RunPod-Fehler 46 war die GPU in `nvidia-smi` sichtbar, aber selbst eine PyTorch-CUDA-Speicherzuweisung funktionierte nicht. Hier ist die konkrete Host-Zuweisung zu prüfen; der Fehler wird nicht durch wiederholte Modell-Downloads behoben.

**Wichtig:** Ein neuer Pod übernimmt den im RunPod-Template gespeicherten Docker Start Command, nicht automatisch die zuletzt auf GitHub dokumentierte Vorlage. Der bekannte alte Commit `68291fd` aus `feature/runpod-p0` startet ausschließlich die ältere TUI. Nur der aktuelle `master/runpod/autostart.sh`-Aufruf startet das German Audiobook Studio automatisch.
