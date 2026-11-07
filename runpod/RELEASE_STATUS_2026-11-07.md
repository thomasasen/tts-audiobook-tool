# German Audiobook Studio — Release-Status und Red-Team-Protokoll

**Stand: 07.11.2026 (Europe/Berlin), master nach Merge von PR #24 (e9085c718969f8c693fc32c74684149302726c75).**
**Release-Entscheidung: KEINE PRODUKTIVFREIGABE.** Es wurden weiterhin keine Produktion mit echter RunPod-GPU, kein deutscher Hördurchgang und kein tatsächlicher Pod-Neustart durchgeführt. Die Code-Integration nach master ersetzt keine reale GPU-Abnahme.

## Aktueller A03-Stand nach PR #24

Die Code-Implementierung für reproduzierbare RunPod-/Modell-Releases ist integriert. Der endgültige PR-Head 8a1504e9d646de3df1b2263538a2841fea457558 bestand alle drei relevanten Workflows: RunPod Static (79 Tests), A03 Release Lock Generation und German Audiobook Studio. Die Lock-Generierung hat fehlende Lockfiles automatisch erzeugt und eingecheckt (Commit 695ba01b1b4273a24774b941437cb05b0a92401c).

- CosyVoice, Matcha-TTS, CosyVoice-Basis-Modell und Thorsten sind auf vollständige Upstream-Revisionen gepinnt.
- Der Modell-Downloader verifiziert Dateien gegen Hugging-Face-LFS-SHA-256 bzw. Git-Blob-SHA-1 der gepinnten Revision. Das private lokale SHA-256-Manifest sichert anschließend die Cache-Integrität.
- Python-/Node-Abhängigkeiten sind mit Hash-Locks gesperrt; Upgrade-/Rollback-Transaktionen und Offline-Regressionen sind implementiert.
- PR #6 wurde mit master verglichen: 389 Commits behind, 20 ahead und divergiert. doctor.py und start.sh sind identisch; die übrigen RunPod-/Autostart-/Setup-Dateien sind ältere Varianten. PR #6 nicht als Ganzes mergen; nur eventuell fehlende Dokumentation separat prüfen.

**Weiterhin nicht ausgeführt:** echte RunPod-Installation, CUDA-/Thorsten-Inferenz, echter Pod-Neustart/Resume, Live-Upgrade-/Rollback, OOM-/Volume-full-Fehlersimulation und menschliche Hörabnahme. Die Produktivfreigabe bleibt deshalb gesperrt.

## Gegenstand

Historische Red-Team-Baseline: PR-Kette #8 → #9 → #10 → #11 → #12 → #13, Runtime-API, SQLite-Jobs, WAV/M4B-Ablauf, RunPod-Skripte und vorhandene Tests. PR #14 lieferte das Betriebs-/Datenkonsistenz-Hardening. PR #24 schließt anschließend die A03-Code-Implementierung für Modellrevisionen, Hash-Locks und Release-Backup/Restore ab.

## Umgesetzte Korrekturen

- Produktionsjob-Anlage per `BEGIN IMMEDIATE` serialisiert. Zweite parallele Anforderung darf keinen aktiven Doppeljob erstellen.
- Zustandsübergänge als bedingte SQL-Updates abgesichert. In-Flight-Abbruch darf Projekt und Job nicht nachträglich als erfolgreich kennzeichnen.
- GPU-Kritikalabschnitt sichert Segment-Retry, Export und Projektlöschung gegeneinander. Vorhandene erfolgreiche Segmente bleiben beim Retry anderer Segmente grundsätzlich cachefähig; die alte M4B-Datei bleibt bis zum atomaren Ersatz auf dem Volume, ist im Neuaufbauzustand nicht downloadbar.
- Vollständigen statt nur ersten WAV-Datenblock auf vorzeitiges Dateiende prüfen, bevor ein Checkpoint wiederverwendet wird.
- RunPod-Cached-Modellgewichte beim Autostart gegen die privat gespeicherten SHA-256-Dateisummen vergleichen. Bei Integritätsfehler **fail closed**, kein automatisches Neuberechnen und Vertrauen einer möglicherweise defekten Datei. Einschränkung: Das private Manifest ist keine externe Signatur/Revision-Pin und ersetzt **keine** vertrauenswürdige Upstream-Prüfsumme.
- Recovery-CLI verlangt echten Fortschritt nach dem Neustart; keine bestandene Abnahme allein wegen neuer Backend-Instanz-ID. Fehlermeldungen der privaten API werden nicht mehr ins Abnahme-Terminal gespiegelt.
- Eindeutigerer Leistungsbegriff: derzeitige Segment-`inference_seconds`-Werte können Modellladen/Retry enthalten. Quotient in der Abnahme als `rtf_generation_wall` statt als isolierter reiner GPU-Inferenz-RTF ausgewiesen.
- Deutsches Original-Test-EPUB um Unterüberschriften, Listen, Zahlen, Fachbegriffe, direkte Rede ergänzt; 12 separate deutsche Fachbuch-Hörtexte und reproduzierbares, blindes Bewertungsschema bereitgestellt.
- README-/RunPod-Standardstart und Tests verwenden `master` bewusst als Entwicklungsstandard; der Release-Modus akzeptiert RUNPOD_TTS_COMMIT für einen unveränderlichen Anwendungscode-Stand. Die Modellrevisionen und Abhängigkeits-Locks sind mit PR #24 umgesetzt.

## Tatsächlich geprüfte Bereiche

| Prüfaspekt | Befund / Evidenz | Status |
| --- | --- | --- |
| PR #8–#13 (historische Bestandsaufnahme) | Review der damaligen PR-Kette; aktuellen Integrationsstand nicht aus dieser Momentaufnahme ableiten | HISTORISCH |
| PR #13 ursprüngliche CI | Studio und RunPod Static erfolgreich auf `090a46d...` | BESTANDEN (alter Commit) |
| PR #14 Backend-/Browser-/Build-CI | GitHub Actions `German Audiobook Studio` auf dem damaligen Hardening-Stand | BESTANDEN (historischer Stand) |
| PR #14 RunPod-Static-CI | `RunPod setup static tests` auf dem damaligen Hardening-Stand | BESTANDEN (historischer Stand) |
| PR #24 A03-Codeabschluss | Merge e9085c7; getesteter PR-Head 8a1504e | IN MASTER |
| RunPod setup static tests #42397452055 | Finaler PR-Head; 79 Offline-RunPod-Tests | BESTANDEN |
| A03 release lock generation #42397452050 | Finaler PR-Head; Lockfiles generiert und eingecheckt | BESTANDEN |
| German Audiobook Studio #42397452058 | Finaler PR-Head; Backend, Frontend, Browser, Web-Runtime und deutscher CPU-ASR-Smoke | BESTANDEN |
| Reale RunPod-GPU-/Pod-Abnahme | Keine echte Pod-Installation, GPU-Produktion oder Neustart-/Resume-Prüfung durchgeführt | OFFEN (E03) |
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
| P1 | DevOps & Security | Upstream-Modellrevisionen sind jetzt gepinnt; Installationsskripte prüfen Dateien gegen HF-LFS-SHA-256 bzw. Git-Blob-SHA-1. Die Vertrauenskette hängt weiterhin vom Hugging-Face-Metadatenendpunkt/TLS ab, nicht von einer unabhängigen Signatur. | Revisionen nur nach erneutem Audit ändern; Upstream-Metadatenprüfung beibehalten und echte Installation auf RunPod abnehmen |
| P1 | Audio | Segmentierung, Ausspracheglossar und artefaktfreie Übergänge für längere reale Fachbücher nicht manuell abgenommen | 12 Texte und mindestens ein längeres Testkapitel menschlich prüfen |
| P1 | Performance | Durchsatz, CUDA-Last, Peak-VRAM und tatsächliche GPU-Kosten nicht gemessen | Echter Benchmark mit konstantem Textset und durchgängiger Timingerfassung |
| P1 | QA & Security | Keine umfangreiche Live-Lastprobe mit vollgelaufenem Volume/OOM, Auth-Abwehr oder mehrfachen Neustarts | Kontrollierte Fehlersimulation am privaten Pod vor Release |
| P2 | UX/Frontend | Kein manueller Desktop-/Mobile-/Kontrast-/Dark-Mode-Red-Team-Review auf produktivem Pod | Browser-E2E ergänzen, manuelles visuelles Review |
| P2 | Audioexport | Cover-/Author-Metadaten und breite Player-Kompatibilität nicht vollständig abgenommen | Separates Kompatibilitätsreview |
| P2 | Dokumentation | Standardstart nutzt `master` bewusst im Entwicklungsmodus; der Release-Modus kann RUNPOD_TTS_COMMIT festsetzen. | Für produktive Runs den geprüften Anwendungscode-Commit explizit angeben; RunPod-Dokumentation auf denselben Ablauf ausrichten |

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


## Zweite Red-Team-Runde: Betriebs- und Datenkonsistenz (07.11.2026)

Der erneute Review nach PR #14 identifizierte weitere konkrete Anwendungsfehler, die im gleichen Draft-Branch korrigiert und mit Regressionstests versehen wurden:

1. **Abbruch sperrte einen erneuten Auftrag:** Nach `cancel` blieb das Projekt in `Warteschlange`, obwohl kein aktiver Auftrag mehr vorhanden war. Die API stellt nun atomar `Vorbereitet` wieder her und die Web-UI bietet **Neuen Versuch starten** für den abgebrochenen Auftrag.
2. **Job-Erstellung gegen gleichzeitige Löschung:** Beim Erstellen wird der Projektstatus jetzt zusätzlich unter dem vorhandenen SQLite-`BEGIN IMMEDIATE` validiert. Eine zwischenzeitliche Löschung kann keinen verwaisten Job mehr erzeugen.
3. **Sprecherfreigabe über Modellwechsel hinweg:** Frühere Hörfreigaben wurden nur an die WAV-Prüfsumme gebunden. Die Freigabe hängt jetzt zusätzlich von der Modellversionsmarke, der privaten Gewicht-Prüfsummenliste, der installierten Inferenzdatei und dem langlebigen Thorsten-Worker-Code ab. Eine Änderung setzt die Freigabe zurück, statt sie stillschweigend weiter gelten zu lassen.
4. **Gemischte Stimmen in einem Buch:** Neue Produktionspläne speichern die Modellidentität; die Wiederaufnahme verweigert Modellwechsel und die WAV-Cache-Fingerprints schließen die Modellidentität ein. Für ältere Pläne ohne diese Kennung werden die Segment-Fingerprints durch die neue Formel ungültig. Die Wiederaufnahme bisheriger Beta-Aufträge muss gesondert überprüft werden.
5. **Browser-Wiederanlauf:** Zusätzlich zu den SQLite/API-Tests prüft der Headless-Browser mit simuliertem abgebrochenem Job, dass die Aktion für einen neuen Vertonungsversuch sichtbar und bedienbar ist. Dies ist **kein** Test einer echten GPU-Erzeugung.

### Grenzen dieser zusätzlichen Prüfung

- Nur Offline-/CPU- und Headless-Browser-Tests stehen in GitHub Actions zur Verfügung; selbst ein grüner Lauf beweist weder eine echte Thorsten-GPU-Ladung noch realistische Audioqualität.
- Die private Modell-Prüfsummenliste bleibt ein **lokaler Konsistenznachweis**. PR #24 ergänzt die Prüfung gegen Upstream-LFS-SHA-256 bzw. Git-Blob-SHA-1 an gepinnten Hugging-Face-Revisionen. Die Vertrauensbasis ist damit die Upstream-Metadatenantwort über TLS, keine unabhängig signierte Attestation.
- Der echte Abbruch während einer laufenden GPU-Inferenz, der Neustart eines Pods und ein langer EPUB→M4B-Durchlauf sind als echte RunPod-Tests weiter offen.
- Nach Änderungen an den Produktionsdateien gelten nur erfolgreiche CI-Läufe des jeweiligen Head-Commits; alte grüne Läufe sind keine Freigabe.

**Produktive Freigabe bleibt gesperrt.** PR #24 wurde nach erfolgreicher CI und expliziter Eigentümerfreigabe in master integriert. Echte GPU-Hörabnahme, vollständiger EPUB→M4B-End-to-End-Lauf sowie Pod-Neustart/Resume und Live-Rollback bleiben vor dem produktiven Einsatz verpflichtend.
