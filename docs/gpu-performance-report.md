# GPU-Performancebericht: Thorsten-CosyVoice3 auf NVIDIA L40S

Stand: 2026-11-08. Ausgangsbasis `master` = `84a64dfa46eb9f59e0a8afc13b6494299293a5ed`.

**Messstatus: Keine echte L40S-Messung verfügbar.** Änderungen an Worker und
Benchmark sind implementiert, GPU-Durchsatz, Energieverbrauch und qualitative
Gleichwertigkeit müssen auf der Zielhardware noch bestätigt werden. Ein
CPU-basierter Protokolltest ist **keine** Leistungs- oder Hörqualitätsmessung.

## 1. Verifizierte Modell-/Runtime-Basis

| Komponente | Gepinnter Stand |
| --- | --- |
| GPU-Ziel | NVIDIA L40S, 48 GiB VRAM (keine lokale L40S vorhanden) |
| CosyVoice | `QwenAudio/CosyVoice@ace7c47f41bbd303aa6bf1ea80e6f9fbd595cd40` |
| Matcha-TTS | `dd9105b34bf2be2230f4aa1e4769fb586a3c824e` |
| Basismodell | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512@29e01c4e8d000f4bcd70751be16fa94bf3d85a18` |
| Thorsten-Finetuning | `Thorsten-Voice/CosyVoice3@3ae98d07d33de0513dc38564ea7765783bd902a7` |
| Thorsten Python | 3.11.18 |
| PyTorch / Torchaudio | 2.3.1 / 2.3.1, cu121-Wheel-Index |
| ONNX Runtime GPU | 1.18.0, gepinnter CUDA-12-Index |
| Web-Python | 3.12.16 |
| Produktionsmodus | `CosyVoice3.inference_sft(text, "thorsten", stream=False, speed=...)` |

Quellen im Repository: `runpod/model-pins.env`,
`runpod/locks/thorsten/pyproject.toml`, `tts_audiobook_tool/web_studio/thorsten_worker.py`.
Offizieller Thorsten-SFT-Pfad und Frontend-Übernahme:
https://huggingface.co/Thorsten-Voice/CosyVoice3/blob/main/infer_thorsten.py .
Die verwendeten CosyVoice-Implementierungen sind unter dem exakt gepinnten
Commit zu prüfen, nicht unter dem sich weiterentwickelnden `main`:
https://github.com/QwenAudio/CosyVoice/tree/ace7c47f41bbd303aa6bf1ea80e6f9fbd595cd40 .

Keine Runtime-, CUDA-, Precision-, Tokenizer- oder Modellgewichts-Pins geändert.

## 2. Tatsächlicher Produktions-Datenfluss

1. Vorhandene Project-V5-EPUB-Extraktion und deutsche Normalisierung erzeugen
   die Version/Fingerprints enthaltende `NarrationPlan`-Segmentliste.
2. `production._run` verwaltet eine sequenzielle Job-Schleife und hält
   `_GPU` während des gesamten Auftrags.
3. Vorhandene, zum Plan passende WAV-Checkpoints werden per Fingerprint,
   Audio-Hash und vollständiger WAV-Prüfung validiert und wiederverwendet.
4. Für fehlende Segmente startet `_synthesize_with_speed` die Synthese am
   persistenten Thorsten-Worker. Der Worker bleibt nach einer Initialisierung
   resident und bedient auch Hörproben.
5. Der Worker nutzt `torch.inference_mode()`, den SFT-Sprecher `thorsten`
   und `stream=False`. Die gepinnte CosyVoice-Runtime liefert Audio-Chunks
   bereits auf der CPU; danach erzeugt `torchaudio.save` PCM16-WAV.
6. Die Eltern-Pipeline validiert PCM-WAV, veröffentlicht es atomar, führt
   Audio-QC und optional Whisper-ASR aus, speichert Checkpoint-Metadaten und
   berechnet die ETA aus tatsächlichen Segmentlaufzeiten.
7. Nach dem jeweils letzten Segment eines Kapitels wird eine hörbare
   Kapitelvoransicht und ein AAC/M4A-Kapitel veröffentlicht; der finale
   M4B-Export bleibt separat.

Das installierte `speech_tokenizer_v3.onnx` wird bei der Konstruktion
gezwungen, `CPUExecutionProvider` zu verwenden. Dieser
Referenzaudio-Tokenizer wird für den festen Thorsten-SFT-Pfad nicht aufgerufen;
die PyTorch-TTS-Komponenten bleiben auf CUDA. Die bestehende einmalige
CUDA-Allokations-/Synchronisationsprüfung beim Workerstart bleibt erhalten.

## 3. Bestätigte Engpässe und Hypothesen

**Im Quellcode bestätigt:**

- Die feste, modellseitig benötigte SFT-Promptsequenz wurde bei jeder
  `frontend_sft`-Ausführung erneut mittels
  `_extract_text_token(...)` in Token und Längen-Tensor konvertiert,
  einschließlich Device-Transfer. Das ist wiederholte identische Arbeit.
- `production._run` führt TTS, WAV-Speicherung, QC, Hash/Checkpoint
  und Kapitel-Assembly im Wesentlichen seriell aus, während `_GPU`
  gehalten wird. Die nächste Inferenz startet erst danach.
- Die bisherige Sidecar-Kennzahl `inference_seconds` bezeichnet die Zeit
  der Synthese-Adapter-Aufrufe einschließlich Prozesskommunikation,
  temporärer WAV-Speicherung, Validierung und gegebenenfalls Retries,
  **nicht** isolierte CUDA-Inferenzzeit.
- Die Checkpoint-Wiederverwendung liest WAV in mehreren Schritten, um
  Hash und PCM-Gültigkeit separat sicherzustellen. Das ist zusätzlicher I/O,
  jedoch eine beabsichtigte Integritätsgarantie.

**Hypothesen, bisher nicht gemessen:**

- Ob CPU-ASR, PCM-QC und Kapitel-Encoding relevante GPU-Wartezeit auf der
  konkreten L40S verursachen, hängt von Segmentlängen, CPU-Kernen,
  Network-Volume-Latenz und ASR-Konfiguration ab.
- Größere Segmente könnten weniger Frontend-Overhead verursachen,
  können aber Prosodie, Sprecherkonsistenz und Wiederverwendbarkeit verändern.
- Zusätzliche CUDA-Streams, Pinned Memory, FP16-Wechsel, Compile,
  TensorRT/vLLM oder parallele Batches könnten Nachteile oder
  Inkompatibilitäten mit dem gepinnten Thorsten-Finetuning haben.
  Der gepinnte `CosyVoice3.inference_sft`-Aufruf belegt keinen sicheren
  Batch-API-Vertrag; das autoregressive LLM-Modul generiert Tokens
  sequenziell. Keine pauschale Aktivierung.

## 4. Umgesetzte Änderungen

- **Fixes SFT-Prompt-Caching:** `prompt_token` und `prompt_token_len`
  werden einmal nach dem Modellstart erzeugt und pro Segment unverändert
  wiederverwendet. Modellparameter, gesprochenes Texteingangssignal,
  Promptinhalt, Sprechtempo und Audioformat bleiben gleich.
- **Reproduzierbarer A/B-Vergleich:** Nur Benchmark-Anfragen können die alte
  Tokenisierung pro Segment durch `uncached_fixed_prompt=true` reproduzieren.
  Die Produktions-API sendet dieses Flag nicht.
- **Opt-in-Profiling:** Nur im expliziten Benchmark-Modus wird CUDA nach
  Inferenz synchronisiert, um Inferenz-Walltime und WAV-Schreibzeit getrennt
  zu erfassen. Die normale Hörbuchproduktion bekommt keine zusätzlichen
  Synchronisationsbarrieren.
- **Benchmark-Harness:** `runpod/benchmark_thorsten.py` nutzt dieselben
  gepinnten lokalen Modellgewichte und Worker-Schnittstelle, synthetische
  deutsche Kurz-, Mittel- und Langtexte, Warm-up, wechselnde Reihenfolge
  für Baseline/Optimierung, echte Audiodauer aus PCM-WAV, SHA256 und
  vollständige Audio-Validierung.
- **GPU-Observability:** Optionales `nvidia-smi`-Sampling erfasst
  GPU-Gesamtauslastung und Device-VRAM-Peak; Worker-Telemetrie liefert
  optional das durch PyTorch allokierte Peak-VRAM. Das sind
  unterschiedliche Speicherkennzahlen und sie werden nicht verwechselt.
- **Safety:** Der Benchmark sperrt die vorhandene
  `.gpu-worker.lock`, verweigert parallele Studio-Nutzung, startet genau
  einen befristeten Worker, löscht temporäre synthetische WAVs wieder und
  liest keine privaten EPUB-Texte. Kein Modell-Download, kein Auto-Install.
  Im Ergebnis stehen nur Kennzahlen, Profilkennungen und Versionsangaben.

Keine Änderungen an Projekt-IDs, Glossar-Rebase, Fingerprints, Checkpoint-
Semantik, Segmentgrößen, Audiocodecs, Audio-QC oder Kapitel-Export.

## 5. Reproduzierbarer L40S-Test auf RunPod

**Voraussetzung:** Bereits installierte und überprüfte Thorsten-Runtime auf
dem Persistent Volume, funktionierende CUDA-Zuweisung. Die Tests verursachen
L40S-Rechenzeit, aber starten **keinen** RunPod-Pod. Ein bestehender laufender
Pod muss ausdrücklich durch den Betreiber bereitgestellt sein.

Der Benchmark muss **allein** laufen: keine laufende Produktion und kein
Web-Studio-Prozess, weil der Webserver den Prozess-Lock beansprucht und eine
eigene Thorsten-Instanz im VRAM behält. Laufende Produktionsjobs nicht dafür
unterbrechen. Bei geplantem Wartungsfenster:

```bash
# 1. Stellen Sie sicher, dass keine Studio-Produktion läuft.
tmux kill-session -t audiobook-web

# 2. Echte CUDA-Zuweisung vorab prüfen (kein Gewichtsdownload).
python3 /workspace/tts-audiobook-tool/runpod/gpu_preflight.py --env thorsten

# 3. Beide Frontend-Varianten im selben Modellprozess messen.
python3 /workspace/tts-audiobook-tool/runpod/benchmark_thorsten.py \
  --profile all --mode compare --warmup 1 --repeats 3 \
  --output /workspace/audiobook-data/benchmarks/l40s-prompt-cache.json

# 4. Kontrolliert Web-Studio wieder starten.
tmux new-session -d -s audiobook-web \
  "bash /workspace/tts-audiobook-tool/runpod/start-web.sh"
```

Falls die `audiobook-web`-Session anders verwaltet wird, den Webprozess
über den tatsächlich eingesetzten Startmechanismus stoppen und starten.
`tmux kill-session` nicht ausführen, solange ein Produktionsjob arbeitet.
Ein bestehendes, aber noch aktives Studio wird durch den Benchmark-Lock
erkannt; der Benchmark **bricht dann ab**, statt heimlich ein zweites
GPU-Modell zu laden.

Weitere kontrollierte Aufrufe:

```bash
# Ausschließlich kurze Texte für Frontend-Overhead-Diagnose:
python3 /workspace/tts-audiobook-tool/runpod/benchmark_thorsten.py \
  --profile short --mode compare --warmup 2 --repeats 10

# Stationäre Leistung der optimierten Variante:
python3 /workspace/tts-audiobook-tool/runpod/benchmark_thorsten.py \
  --profile all --mode cached --warmup 2 --repeats 5 \
  --output /workspace/audiobook-data/benchmarks/l40s-optimized.json

# CPU-only: CLI- und Benchmark-Regressionen ohne Modell-Download:
cd /workspace/tts-audiobook-tool
python3 -m unittest discover -s tests/runpod -p 'test_*.py' -v
```

Die JSON-Datei enthält Modellstart, Audiolänge, isolierte Inferenz-
und WAV-Zeiten, lokale Validierungszeit, RTFs, Durchsatz, GPU/VRAM-Samples
und optional die prozentuale RTF-Veränderung für das feste Prompt-Caching.
Die Auswertung **schließt** das Studio-interne Whisper-ASR, SQLite/
Checkpoint-Verwaltung, Kapitel-M4A und M4B aus. Der angegebene
`pipeline_rtf` ist ausdrücklich der **Benchmark-Pipeline-RTF**,
nicht die gesamte Hörbuch-Produktionszeit.

Für belastbare Messungen: RunPod-GPU-Modell, Leistungszustand,
CPU-Zuweisung, ASR-Modus, Workload und alle Konfigurationen konstant halten;
Warm-up getrennt betrachten; Ergebnisse wiederholen. Unter Umständen ist
ein zweiter Profil-Lauf bei deaktiviertem `nvidia-smi`-Sampling hilfreich,
um Mess-Overhead zu quantifizieren. Bei unverändertem Klangprofil
Blindhörprobe und regulären Recovery-Test zusätzlich durchführen.

## 6. Vorher/Nachher

| Maßnahme/Kennzahl | Vorher | Nachher | Veränderung | Messstatus |
| --- | --- | --- | --- | --- |
| Modellstart | Nicht gemessen | Nicht gemessen | Nicht gemessen | L40S offen |
| Inferenz-RTF | Nicht gemessen | Nicht gemessen | Nicht gemessen | A/B-Befehl vorbereitet |
| Benchmark-Pipeline-RTF | Nicht gemessen | Nicht gemessen | Nicht gemessen | L40S offen |
| Vollständiger Studio-Pipeline-RTF | Nicht gemessen | Nicht gemessen | Nicht gemessen | Separater E2E-Lauf nötig |
| Audio-Durchsatz | Nicht gemessen | Nicht gemessen | Nicht gemessen | L40S offen |
| GPU-Auslastung | Nicht gemessen | Nicht gemessen | Nicht gemessen | Sampling vorbereitet |
| VRAM-Peak | Nicht gemessen | Nicht gemessen | Nicht gemessen | Sampling vorbereitet |
| Gesamtlaufzeit pro Kapitel | Nicht gemessen | Nicht gemessen | Nicht gemessen | E2E-Lauf offen |
| Gesamtlaufzeit des Buchs | Nicht gemessen | Nicht gemessen | Nicht gemessen | E2E-Lauf offen |

Es gibt keine plausible Grundlage, aus dem Umfang der Änderungen eine
Prozentzahl zur Gesamtersparnis abzuleiten. Die CPU-seitigen Tests sichern
Verhalten, nicht Laufzeitvorteile.

## 7. Nicht eingeführte Optimierungen und Risiken

| Kandidat | Entscheidung / Begründung |
| --- | --- |
| GPU/CPU-Overlap durch Queue | **Nicht eingeführt:** GPU-Lock umfasst Glossar-Update, Pausieren, Resume, atomare Sidecars und Kapitel-Encoding. Korrekte Reihenfolge/Invalidierung erfordert eigene Implementierung und echte E2E-Abnahme. |
| Segment-Merging | **Nicht eingeführt:** Stimme, Betonung, Quellen-Fingerprints und selektive Glossar-Neuberechnung könnten verändert werden. |
| Batching und zusätzliche Modellinstanzen | **Nicht eingeführt:** Kein verifizierter SFT-Batchvertrag des gepinnten Pfads, konkurrierende VRAM- und Thread-Nutzung. |
| Stream=True | **Nicht eingeführt:** Chunk-Layout und Satzübergänge können sich verändern; `stream=False` ist derzeit der dokumentierte Thorsten-Fall. |
| FP16, TF32, `torch.compile`, CUDA-Streams, Pinned Memory | **Nicht eingeführt:** Nur nach isoliertem Benchmark und Klang-/Stabilitätstest. |
| TensorRT-LLM/vLLM/optimierte Decoder | **Nicht eingeführt:** Upstream-Features sind kein Nachweis der Kompatibilität mit dem exakt gepinnten Thorsten-Finetuning. Neue Runtime/Weights bedürfen separater Freigabe. |
| Entfernen von WAV-Validierung/Hashes | **Abgelehnt:** Gefährdet sichere Wiederaufnahme und Datenintegrität. |

**Freigabestatus:** Risikoarme Änderung mit produktionsseitig unverändertem
Audioformat und unveränderten Text-/Voice-Parametern; dennoch **kein
behaupteter Performancegewinn und keine finale L40S-/Hörqualitäts-Abnahme**.
Nach jeder Änderung an `thorsten_worker.py` verlangt die bestehende
`_model_identity`-Prüfung eine erneute Hörprobe/Freigabe des Modells.
Vor großflächiger Hörbuchproduktion sollten Benchmark, Klangvergleich und
Neustart-/Resume-Prüfung auf RunPod erfolgen.
