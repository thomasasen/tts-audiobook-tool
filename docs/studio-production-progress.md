# German Audiobook Studio – Implementierungs- und Abnahmeprotokoll

Stand: 06.11.2026. **Kein Produktions- oder GPU-Test wurde behauptet.**

## Branch-/PR-Abhängigkeiten

- PR #8: German-first Design-Spezifikation, Basis master.
- PR #9: React/FastAPI und Kapitelvorbereitung, Basis PR #8.
- PR #10: deutsche EPUB-Erkennung, Basis PR #9.
- Dieser Branch: Verarbeitung, Glossar, Audio/Export und RunPod-Integration, Basis PR #10.
- PR #6: separates RunPod-TUI-Setup auf master; ein späterer Integrations-PR muss RunPod-Änderungen sauber übernehmen. Nicht blind beide Varianten mergen.

## Architektur

1. app.py bleibt die einzige authentifizierte Web-API. Keine zweite Weboberfläche.
2. Projektinformationen und Kapiteltext stammen aus dem vorhandenen Project-V5-Modell (engine/project.json, engine/project_text.json/book.v3). Kein zweiter EPUB-Parser.
3. word_substitutions ist das einzige persistente Ausspracheglossar. Die vorhandene PromptNormalizer-Funktion wird verwendet.
4. production.py nutzt einen einzelnen GPU-Worker, SQLite-Zustände, wiederverwendbare WAV-Segmente und atomare Datei-Commits. Daten liegen außerhalb von Git.
5. Der vorhandene AacWriter übernimmt M4B mit Kapitelmarken.
6. Stimme: Thorsten-Voice/CosyVoice3 ist ein fertig trainiertes deutsches Modell (Apache-2.0, CC0-Daten). Keine nutzerseitigen Referenzen und kein Stimmkloning. Ohne lokale Engine und echte angehörte Hörprobe bleibt die Produktion gesperrt.
7. TUI und bestehende Engine bleiben unberührt.

## Zwischenzeitlich implementiert im Integrations-PR #12

- Curated Thorsten-CosyVoice3-Python-3.11-Installation auf persistentem Volume, deutscher Webstart, ursprüngliche TUI erhalten
- Dauerhaft geladener CosyVoice3-Modellprozess, ohne erneutes Laden bei jedem Segment
- Optionaler Whisper-basierter deutscher ASR-Abgleich und konservative Hinweise zu Stille/Clipping; konkrete Funde können abgehört und einzelne Segmente wiederholt werden
- Echtes M4B-/AAC-Containerformat samt Kapitelmarken mit synthetischen WAV-Testdaten automatisiert geprüft (kein TTS-Hörtest)
- Sicherer Web-Token, segmentweise persistente Verarbeitung, Wiederaufnahme auf Neustart

## Offene Release-Gates

- [ ] Thorsten CosyVoice3 auf RunPod installieren, CLI verifizieren, deutsche Hörprobe und Freigabe
- [ ] Blindhörvergleich CosyVoice3 / Qwen3-TTS 1.7B / MOSS v1.5 bei gleichem deutschen Fachtext
- [ ] Mehrere reale EPUBs mit deutschen Inhaltsverzeichnissen und Vorwort testen
- [ ] Tatsächlichen Neustart, Resume und Abbruch im Langzeittest verifizieren
- [ ] M4B-Kapitel mit ffprobe und mehreren Playern auf **RunPod-generierten** Audiodateien prüfen; PyAV-Container-/Decode-Test mit synthetischer Spracheingabe vorhanden
- [ ] Whisper-ASR und Qualitätsflagging mit **echten generierten deutschen Audiosegmenten** validieren (Code integriert, keine GPU-Hörabnahme)
- [ ] Cover, Autorenmetadaten und gegebenenfalls MP3 ergänzen
- [ ] Browser-End-to-End-Tests und visuelles Review samt Barrierefreiheit und Fehlerzuständen
- [ ] Last-/Security-Test mit vollem Datenträger und problematischen EPUB-Dateien

Automatisierte Tests belegen Funktionalität, keine Sprachqualität. Die Integration nach `master` erfordert ausdrückliche Eigentümerfreigabe und grüne CI; für die **produktive Freigabe** sind zusätzlich reale GPU-Hörtests und Recovery-Nachweise verbindlich. Große Debug- und Testarchive nicht in Git speichern.
