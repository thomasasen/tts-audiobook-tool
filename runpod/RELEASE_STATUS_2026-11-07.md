# German Audiobook Studio — Release-Status und Red-Team-Protokoll

**Stand: 07.11.2026 (Europe/Berlin), Entwicklungsbranch `feature/german-audiobook-release-hardening`, Draft-PR #14.**
**Release-Entscheidung: NICHT FREIGABEFÄHIG.** Keine Produktion mit echter RunPod-GPU, kein deutscher Hördurchgang und kein tatsächlicher Pod-Neustart wurden in diesem Auftrag durchgeführt. Kein Merge nach `master`.

## Gegenstand

Untersucht: PR-Kette #8 → #9 → #10 → #11 → #12 → #13, Runtime-API, SQLite-Jobs, WAV/M4B-Ablauf, RunPod-Skripte und vorhandene Tests. PR #6 ist ein separater RunPod-Arbeitszweig und bleibt bei späterer Integration abzugleichen. Der neue PR #14 enthält Hardening auf Basis von #13, keinen konkurrierenden zweiten API- oder Modellstack.

## Umgesetzte Korrekturen

- Produktionsjob-Anlage per `BEGIN IMMEDIATE` serialisiert. Zweite parallele Anforderung darf keinen aktiven Doppeljob erstellen.
- Zustandsübergänge als bedingte SQL-Updates abgesichert. In-Flight-Abbruch darf Projekt und Job nicht nachträglich als erfolgreich kennzeichnen.
- GPU-Kritikalabschnitt sichert Segment-Retry, Export und Projektlöschung gegeneinander. Vorhandene erfolgreiche Segmente bleiben beim Retry anderer Segmente grundsätzlich cachefähig; die alte M4B-Datei bleibt bis zum atomaren Ersatz auf dem Volume, ist im Neuaufbauzustand nicht downloadbar.
- Vollständigen statt nur ersten WAV-Datenblock auf vorzeitiges Dateiende prüfen, bevor ein Checkpoint wiederverwendet wird.
- RunPod-Cached-Modellgewichte beim Autostart gegen die privat gespeicherten SHA-256-Dateisummen vergleichen. Bei Integritätsfehler **fail closed**, kein automatisches Neuberechnen und Vertrauen einer möglicherweise defekten Datei. Einschränkung: Das private Manifest ist keine externe Signatur/Revision-Pin und ersetzt **keine** vertrauenswürdige Upstream-Prüfsumme.
- Recovery-CLI verlangt echten Fortschritt nach dem Neustart; keine bestandene Abnahme allein wegen neuer Backend-Instanz-ID. Fehlermeldungen der privaten API werden nicht mehr ins Abnahme-Terminal gespiegelt.
- Eindeutigerer Leistungsbegriff: derzeitige Segment-`inference_seconds`-Werte können Modellladen/Retry enthalten. Quotient in der Abnahme als `rtf_generation_wall` statt als isolierter reiner GPU-Inferenz-RTF ausgewiesen.
- Deutsches Original-Test-EPUB um Unterüberschriften, Listen, Zahlen, Fachbegriffe, direkte Rede ergänzt; 12 separate deutsche Fachbuch-Hörtexte und reproduzierbares, blindes Bewertungsschema bereitgestellt.
- README-/RunPod-Startbranch und Tests auf den PR-#14-Entwicklungszweig ausgerichtet. **Vor einem finalen Release auf gepinnte Commit-/Modellrevisionen umstellen.**

## Tatsächlich geprüfte Bereiche

| Prüfaspekt | Befund / Evidenz | Status |
| --- | --- | --- |
| PR #8–#13 aktuell | Alle offen und Draft; gerichtete Basis-Branches bestätigt | BESTANDEN (Bestandsaufnahme) |
| PR #13 ursprüngliche CI | Studio und RunPod Static erfolgreich auf `090a46d...` | BESTANDEN (alter Commit) |
| PR #14 Backend-/Browser-/Build-CI | GitHub Actions `German Audiobook Studio`: standabhängige Runs; aktuelle Head-SHA im Workflow kontrollieren | AKTUELLEN HEAD NACHPRÜFEN |
| PR #14 RunPod-Static-CI | `RunPod setup static tests`: neuere Runs nach Änderungen prüfen | AKTUELLEN HEAD NACHPRÜFEN |
| EPUB-Parser und Project-V5 | Vorhandene echte Import-/Persistenztests in CI; private Realbücher nicht genutzt | AUTOMATISIERTER TEST, KEIN PRODUKTIONSBUCH |
| AAC/M4B mit Kapitelmarken | Bereits vorhandener PyAV-Export-/Decode-Test mit **synthetischem Audio**, nicht GPU-TTS | AUTOMATISIERTER TEST |
| Browser-Anmeldung/Kapitel/Glossar | Vorhandene Chromium-Smoke-Tests in CI; keine manuelle UI-Abnahme auf Live-Pod | AUTOMATISIERTER TEST |
| Thorsten-Download und CUDA-Laden | Weder echter Pod noch eigene GPU verfügbar | BLOCKIERT |
| Menschlich angehörte deutsche Hörprobe | Keine reale Abhörung durchgeführt | NICHT DURCHGEFÜHRT |
| Vollständiges, tatsächlich gesprochenes EPUB→M4B | Ohne zugängliche CUDA-GPU und freigegebenen Sprecher nicht ausführbar | BLOCKIERT |
| Echte Pod-Neustart-/Resume-Abnahme | Testskript vorbereitet, kein kontrollierbarer Pod vorhanden | BLOCKIERT |
| Realer RTF, VRAM-Peak, Kosten/Hörstunde | Keine GPU-Runtime-Messungen; keine Daten zu behaupten | NICHT DURCHGEFÜHRT |
| Blindhörvergleich Qwen/MOSS/Thorsten | 12 Prüftexte und Verfahrensdokument verfügbar; keine Hörproben | NICHT DURCHGEFÜHRT |

**Wichtig:** Jede CI-Freigabe ist an die exakte Head-SHA des jeweiligen erfolgreichen Runs gebunden. Eine bloße grüne Anzeige eines älteren Commits darf nicht auf nachfolgende Commits übertragen werden.

## Risikoregister nach Red-Team-Perspektiven

| Priorität | Perspektive | Gegenstand | Maßnahme / Rest |
| --- | --- | --- | --- |
| P0 / Release-Blocker | AI/ML & Audio | Fehlende nachgewiesene Texttreue, deutsche Aussprache und WAV→M4B mit realer TTS | Echter GPU-Lauf, ASR-Auffälligkeiten hören, Kapitel und alle Textpassagen prüfen |
| P0 / Release-Blocker | DevOps & QA | Wiederaufnahme nach echtem Pod-Neustart nicht belegt | `status --checkpoint`, kontrollierter Pod-Neustart, `status --verify-restart`, danach `finish` |
| P1 | DevOps & Security | Modellgewichte ohne extern gepinnte HF-Revisionshashes; privates SHA-256-Manifest prüft nur lokale Integrität nach Installation | Vor einem finalen Release eindeutig verfügbare Upstream-Revisions-IDs und Vertrauensanker pinnen |
| P1 | Audio | Segmentierung, Ausspracheglossar und artefaktfreie Übergänge für längere reale Fachbücher nicht manuell abgenommen | 12 Texte und mindestens ein längeres Testkapitel menschlich prüfen |
| P1 | Performance | Durchsatz, CUDA-Last, Peak-VRAM und tatsächliche GPU-Kosten nicht gemessen | Echter Benchmark mit konstantem Textset und durchgängiger Timingerfassung |
| P1 | QA & Security | Keine umfangreiche Live-Lastprobe mit vollgelaufenem Volume/OOM, Auth-Abwehr oder mehrfachen Neustarts | Kontrollierte Fehlersimulation am privaten Pod vor Release |
| P2 | UX/Frontend | Kein manueller Desktop-/Mobile-/Kontrast-/Dark-Mode-Red-Team-Review auf produktivem Pod | Browser-E2E ergänzen, manuelles visuelles Review |
| P2 | Audioexport | Cover-/Author-Metadaten und breite Player-Kompatibilität nicht vollständig abgenommen | Separates Kompatibilitätsreview |
| P2 | Dokumentation | Startkommando derzeit bewusst PR-#14-Branch statt immutable Commit; Dokumentation enthält teils alte TUI-Abschnitte | Vor Stable-Release pinnen und Dopplungen reduzieren |

**Red-Team-Einschränkung:** Der Code-Review ist statisch. Ohne echte GPU, belastbaren Lautheits-/Sprachdatensatz und Live-Pod kann weder die Abwesenheit aller P0/P1-Probleme noch die Produktionsreife bescheinigt werden.

## Nächste reale Abnahme auf RunPod

**Keine RunPod-Instanz wurde bestellt oder gestartet.** Für den nächsten Lauf einen selbst autorisierten Pod mit NVIDIA CUDA, persistentem Volume unter `/workspace`, empfohlen zunächst 24 GB VRAM und mindestens 100 GB Volume nutzen. Die korrekte Entwicklungsbranch-Startzeile steht zu Beginn der [README](../README.md). Privaten Token nur innerhalb des Pods auslesen.

Nach Thorsten-Installation im Browser die echte Hörprobe erzeugen, anhören, ausdrücklich freigeben. Dann in der privaten Pod-Shell:

```bash
cd /workspace/tts-audiobook-tool
PY=/workspace/audiobook-data/venvs/qwen/bin/python
"$PY" runpod/acceptance.py preflight
"$PY" runpod/acceptance.py start --repeat 3
"$PY" runpod/acceptance.py status
# Erst bei Status generated:
"$PY" runpod/acceptance.py finish
```

Für echten Neustart mit neuem längeren Auftrag wie in [ACCEPTANCE.md](ACCEPTANCE.md) dokumentiert verfahren. `finish` allein bestätigt technische M4B-Merkmale und ersetzt nicht die Hörabnahme. **Master-Merge weiterhin gesperrt bis zur expliziten Freigabe und erfolgreichen echten Abnahme.**
