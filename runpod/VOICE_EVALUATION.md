# Deutsche Hörabnahme und Modellvergleich

**Stand 06.11.2026:** Verfahren und selbst verfasste Testtexte vorbereitet. **Keine realen GPU-Messungen oder Hörfreigaben durchgeführt.**

## Voraussetzungen

Alle Kandidaten verwenden dieselben zwölf unveränderten deutschen Textpassagen aus [voice_testset.json](voice_testset.json). Testartefakte bleiben ausschließlich im privaten RunPod-Volume unter /workspace/audiobook-data/acceptance.

Thorsten-CosyVoice3 bleibt zunächst der Hauptkandidat. Qwen3-TTS CustomVoice und MOSS-TTS v1.5 sind nur Vergleichskandidaten. Ausschließlich vorgefertigte rechtlich nutzbare Sprecherprofile verwenden, **keine** Nutzeraufnahmen, Stimmklone oder frei designten Sprecher.

## Blindhörverfahren

1. Echte NVIDIA-GPU nachweisen und sämtliche Modelldaten mit Revision, Lizenz, CUDA-/Torch-Version, GPU, Speicher und Konfiguration aufnehmen.
2. Alle zwölf Texte identisch mit jedem verfügbaren und zulässigen Kandidaten rendern, die WAV-Dateien im privaten Volume speichern und per Decoder prüfen.
3. Ein nicht mitbewertender Operator mischt die WAV-Dateien unter A/B/C-Pseudonymen. Die Modellzuordnung bleibt bis zur abgeschlossenen Beurteilung geheim. Lautheit vergleichbar einstellen, aber Pausen und Prosodie nicht manipulieren.
4. Jede Probe vollständig abhören. Eigennamen, deutsche und englische Fachbegriffe, lange Sätze, Auslassungen, Wiederholungen, Klipping, Pausen und Segmentgrenzen dokumentieren.
5. Zwei unabhängig bewertete Hördurchgänge und zusätzliche zusammenhängende Fachbuchkapitel empfehlen sich, um längere Stimmstabilität zu beurteilen.
6. Bei schweren Auslassungen, unklarem Lizenzstatus oder nicht nachvollziehbarer Freigabe gilt das Modell trotz hoher Punktzahl als **nicht abgenommen**.

## Bewertungsraster (1 = mangelhaft, 5 = sehr gut)

| Kriterium | Gewicht |
| --- | ---: |
| Natürlichkeit und Prosodie | 30 % |
| Deutsche Aussprache | 25 % |
| Fachbegriffe und englische Begriffe | 20 % |
| Stimmkonsistenz | 15 % |
| Audioartefakte und Segmentübergänge | 10 % |

Gesamtscore = 0,30 × Natürlichkeit + 0,25 × Deutsch + 0,20 × Fachbegriffe + 0,15 × Konsistenz + 0,10 × Artefakte.

Ein Score ohne tatsächlich gehörte Dateien ist **kein Testergebnis**. Jede Punktzahl benötigt einen namentlich dokumentierten Hördurchgang und einen Zeitpunkt.

## Messprotokoll und Kosten

Für jedes Modell getrennt erfassen: Modellrevision, GPU, VRAM-Spitzenwert, Python/Torch/CUDA, Kalt-/Warmladezeit, reine Inferenzzeit, Retry- und Wartezeiten, Postprocessing-Zeit, Anzahl Segmente, gültige Audiodauer, Exportzeit und tatsächlichen RunPod-Tarif.

RTF = **reine gemessene Inferenzzeit / erzeugte Audiodauer**. Die bestehende Segment-Stoppuhr im Studio umfasst unter Umständen auch kaltes Modellladen und Retries: daher vorläufig **nicht** als reine GPU-Inferenz-RTF verwenden. Für Wirtschaftlichkeit zusätzlich tatsächliche Gesamtzeit inklusive Modellladen verwenden. Fehlende Werte bleiben offen, nicht schätzen.

## Modellkandidaten und Rechtslage

| Modell | Verifizierte Grundlage | Was noch getestet werden muss |
| --- | --- | --- |
| [Thorsten-CosyVoice3](https://huggingface.co/Thorsten-Voice/CosyVoice3) | Deutsches Finetuning, Apache-2.0, Thorsten-Trainingsdaten CC0, CosyVoice-Revision ace7c47 | Natürliche deutsche Hörprobe, langer EPUB-Lauf und GPU-Performance |
| [Qwen3-TTS 1.7B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) | Deutsch offiziell unterstützt, feste Sprecherprofile, Apache-2.0 | Hörtest mit geeigneter fester deutscher Stimme; keine US-geprägten Fallbacks |
| [MOSS-TTS v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Deutsch unter 31 Sprachen, Apache-2.0, vergleichsweise große Modelldateien | Rechtlich einwandfreies fertiges Sprecherprofil, GPU- und Hörqualität |

## Leere Ergebnisvorlage

| Feld | Wert |
| --- | --- |
| Status | NICHT DURCHGEFÜHRT |
| Testset | runpod/voice_testset.json |
| Modell und Revision | offen |
| GPU und VRAM-Spitze | offen |
| A/B/C-Pseudonyme und Zuordnung | privat dokumentieren |
| Hörnoten der fünf Kriterien | offen |
| Gewichtete Gesamtnote | offen |
| Freigabe durch reale Testperson | offen |
| Gemessener RTF | offen |
| Art und Anzahl von Fehlern | offen |

Die Modelle dürfen **nicht** allein wegen veröffentlichter Benchmarks oder theoretischer Modellgröße ausgetauscht werden. Der Produktionswechsel benötigt manuelle Hörfreigabe.
