# Optionale parallele Thorsten-Hörbuchproduktion auf RunPod

Stand: 2026-11-08. **Implementiert, CPU-Protokoll-/Projektintegrationstests vorhanden;
ein echter GPU-Durchsatz- und Hörqualitätsvergleich ist noch ausstehend.**

## Zweck und Grenzen

**Standard seit 2026-11-08: zwei Worker für neue Aufträge**, ohne dass eine
RunPod-Umgebungsvariable angelegt werden muss. Neue NarrationPlan-v3-Jobs
können zwei unabhängige, persistente Thorsten-CosyVoice3-SFT-Prozesse auf
derselben L40S einsetzen. Unterstützte Werte: `1`, `2` (Standard), `3`, `4`.

Die Anzahl wird beim Erstellen des Auftrags als
`parallel_worker_count` in seinem privaten Plan eingefroren. Für alle
vorhandenen Aufträge ohne diesen Schlüssel gilt **immer ein Worker**,
auch nach einem Neustart oder einer Host-Konfigurationsänderung.
Eine nachträgliche Reduzierung der Host-Einstellung auf `1` erzwingt
auch für bereits parallel geplante Jobs einen sicheren seriellen Betrieb.

Der Parallelmodus ist **kein echtes Batch-Inferencing**. Jeder Prozess lädt
die identischen gepinnten Modellgewichte unabhängig und erhält genau einen
SFT-Aufruf zur Zeit. Das kann die GPU besser beschäftigen, erhöht aber den
VRAM-Verbrauch. Es gibt **keine zugesicherte Beschleunigung**.
Ein höherer Worker-Wert ist nicht automatisch schneller.

## Kontrollierte Aktivierung für einen neuen Auftrag

* Ein bereits laufendes Hörbuch **nicht** unterbrechen oder den Pod dafür
  neustarten. Der bestehende Auftrag nutzt weiterhin seinen eingefrorenen
  seriellen Modus.
* Nach Abschluss der Produktion im RunPod-Pod die GPU frei geben.
* Bei unverändertem Startskript werden für **neue** Aufträge automatisch
  zwei Worker verwendet. Eine manuelle Einstellung ist dafür nicht nötig.
* Für eine andere Workerzahl in RunPod unter **Pods → Edit Pod →
  Environment Variables** die Variable `AUDIOBOOK_STUDIO_TTS_WORKERS`
  mit dem gewünschten Wert `1`, `2`, `3` oder `4` setzen. Änderungen an
  einer laufenden Pod-Konfiguration führen zu einem Neustart. Alternativ
  vor einem **manuellen** Webserverstart aus derselben Shell
  `export AUDIOBOOK_STUDIO_TTS_WORKERS=1` ausführen.
* **Nicht während einer laufenden Produktion** an der Pod-Konfiguration
  arbeiten. Der aktive Auftrag bleibt dank eingefrorenem Plan zwar seriell,
  aber ein Pod-Neustart unterbricht die Arbeit vorübergehend.
* Neue Hörprobe und anschließend **ein kleines Testbuch** erzeugen. Mit
  `1` und `2` Workern dieselben synthetischen Texte und Speed-Einstellungen
  verwenden; Dauer, GPU-Spitzenlast, VRAM, ASR-Warnungen und Audio manuell vergleichen.
* Erst danach `3` bzw. `4` Worker testen, falls `2` messbar besser und
  stabil sind. Für die Rückkehr zum Standard `AUDIOBOOK_STUDIO_TTS_WORKERS=1`
  setzen und den Webserver ohne laufenden Job neu starten.

`runpod/benchmark_thorsten.py` ist weiterhin ein **serieller
Einzelworker-Benchmark** und kein Nachweis für Multiworker-Gewinne.
Das Benchmark-Skript verweigert Aufrufe, während das Studio seine
exklusive GPU-Sperre hält.

## Sicherheitsarchitektur

* Die globale Prozesssperre `.gpu-worker.lock` und `_GPU` bleiben aktiv.
  Der Pool existiert ausschließlich innerhalb eines Hörbuchauftrags.
  Studio-Hörproben, Glossarmigration und Export konkurrieren nicht
  mit parallelen Aufträgen.
* Höchstens N unabhängige Requests und N ausstehende
  `*.partial.wav`-Dateien. EPUB-Originale und der NarrationPlan
  werden nicht von den Worker-Prozessen bearbeitet.
* Die Produktionsschleife publiziert **in ursprünglicher Reihenfolge**
  geprüfte PCM16-WAVs und die atomaren Sidecars mit Segment-Fingerprints
  und SHA-256. Qualitätsprüfung und Kapitel-M4A bleiben unverändert.
* Pausieren, Abbrechen und Prozessende dürfen keine unvollständigen
  WAV-Dateien als abgeschlossen markieren. Ausstehende Kandidaten werden
  beim Abbau des Pools verworfen; das vorhandene Recovery bereinigt
  `*.partial.wav` auch nach einem Pod-Neustart.
* Es gibt weiterhin höchstens drei Versuche pro fehlerhaftem Segment.
  Nach einem Workerfehler wird die betreffende Modellinstanz geschlossen
  und beim nächsten Versuch neu gestartet.
* Fehlertexte und private Buchpassagen werden nicht in CI, HTTP-Antworten
  oder öffentlichen Logs ausgegeben. Pro Worker gibt es ein separates
  privates RunPod-Log.
* Die Datei `thorsten_worker.py`, Modellgewichte, Sprecher-ID, Speed-Limits,
  Torch-/CUDA-Pins und PCM16-Format wurden nicht verändert.

## Unbedingt auf echter L40S prüfen

1. Gleiche Kapitel mit einem und zwei Workern, gleiche Sprechgeschwindigkeit:
   Audiominuten je Verarbeitungsminute sowie gesamte Produktionszeit.
2. Spitzen-VRAM für 2/3/4 Prozesse und stabilen Betrieb ohne CUDA OOM.
3. Stimmkonsistenz: Aussprache aus dem Glossar, Prosodie, Dauer,
   Artefakte, ASR-Befunde und Hörvergleich aller generierten Kapitel.
4. Pausieren beim Start zweier laufender Segmente; anschließend
   fortsetzen. Glossaränderung am Segmentübergang, kontrollierter
   Container-Neustart und Checkpoint-Wiederverwendung.
5. Unvollständige WAVs und Sidecars dürfen weder für Resume noch für
   den fertigen M4B-Export akzeptiert werden.

**Freigabestatus:** Standard für neu angelegte Aufträge ist nun 2. Die
L40S-Performance und die tatsächliche Audioqualität mit zwei Arbeitern
sind noch nicht mit dem echten Modell abgenommen. Bereits vorhandene
Aufträge ohne `parallel_worker_count` behalten unverändert einen Worker.
Aktuelle Produktionen nicht für den Konfigurationswechsel unterbrechen.
