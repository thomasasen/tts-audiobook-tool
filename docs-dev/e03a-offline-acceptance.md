# E03-A · GPU-freie Offline-Abnahme

Geprüft am 08.11.2026 (Europe/Berlin) auf `test/e03a-offline-acceptance`, ausgehend von `master` bei `f9b51b85259c02b9708877c71eba50792865889e`.

Der lokale technische Kernlauf vom synthetischen EPUB bis zum vollständig decodierten M4B einschließlich echtem Dienstneustart ist bestanden. Die vollständige RunPod-Regression benötigt Linux. Der lokale Windows-Versuch und der Linux-Workflow werden getrennt ausgewiesen. Das Release-Gate E03 bleibt bis zur GPU- und Hörabnahme offen.

## Umgebung und Ausgangslage

| Merkmal | Geprüfter Stand |
|---|---|
| Ausgangsbasis | Lokaler `master`, `origin/master` und GitHub-`master` stimmten überein. [B03, PR #31](https://github.com/thomasasen/tts-audiobook-tool/pull/31) war integriert. Offene PRs vor dem Start waren #1, #6 und #25. |
| CI auf dem Basiscommit | Alle fünf Jobs erfolgreich, einschließlich Backend, Frontend, Chromium, isolierter Web-Runtime und CPU-ASR. [Studio-Lauf 42431917263](https://github.com/thomasasen/tts-audiobook-tool/actions/runs/42431917263) |
| Lokaler Host | Windows NT 10.0.26300, x64, Git 2.56.0.windows.3, Git Bash 5.3.15. WSL fehlte. Windows erlaubte dem Testkonto keine Symlinks. |
| Python | Isoliertes Venv mit CPython 3.12.14, Pytest 8.4.2 und Torch 2.8.0+cpu (`torch.version.cuda is None`). Beide Lockdateien separat mit `--require-hashes --no-deps` installiert, Windows- und Torch-Zusatzpakete separat. `pip check` war erfolgreich. |
| Node und Browser | Node 26.13.0, npm 12.2.1, Playwright 1.56.0 mit Chromium Headless Shell 141.0.7390.37. |
| Audio | PyAV 18.1.0 mit FFmpeg 8.1.2, libavcodec 62.28.102 und libavformat 62.12.102. Die Playwright-FFmpeg-CLI ist ein reduzierter VP8-Build ohne AAC. Studio nutzt die FFmpeg-Bibliotheken von PyAV für echtes AAC-Encoding, MP4-Muxing und Decoding. |
| Isolation | Selbst verfasstes EPUB aus `runpod/acceptance_epub.py`, synthetische Zugangsdaten, temporäre Datenwurzeln und Loopback-HTTP. Test-Doubles für Modellverfügbarkeit, Modellidentität und Inferenz. Im Review erhielt jede Einheit des Testbuchs über ihren Index einen eigenen PCM-Testton. Die Aufrufspur prüft davon getrennt die eingefrorenen Prompts. ASR blieb lokal im E2E deaktiviert. `doctor.py` fragte Hardwaredaten ab. Die Produktionstests nutzten ausschließlich CPU. |
| Windows-Testadapter | Nur im temporären `PYTHONPATH` lag ein `fcntl`-Adapter auf Basis einer exklusiven, nicht blockierenden Windows-Dateisperre. Linux verwendet im Produkt und in CI die native `flock`-Sperre. |

## Testmatrix

| ID | Prüfung | Methode und Nachweis | Ergebnis |
|---|---|---|---|
| A01 | ZIP, EPUB-Struktur, Spine und Navigation | CRC, unkomprimiertes erstes `mimetype`, XML-Container, Manifest, Ressourcen und Navigation in `test_offline_acceptance.py`. Produktive Navigation und Spine-Sonderfälle in `test_epub_extractor.py`. | PASS |
| A02 | Vorwort, Kapitelwahl, Reihenfolge und Texttreue | HTTP-Upload und echte Project-V5-Auswahl. Drei erwartete Kapitel, Wiederholungen mit exakter Anzahl bei 2 und 50 Absätzen pro Kapitel, Inhaltsverzeichnis und technische Attribution ausgeschlossen. Gleichnamige Kapitel und Überschriften über EPUB-, Plan- und Exporttests. | PASS |
| A03 | Unterüberschriften, Listen, Tabellen, Unicode, Quellen und Sprecherfassungen | `test_epub_content_audit.py`, `test_epub_css.py`, `test_real_epub_e2e.py`, Project-V5-Serialisierung. Erhaltene Tabellenzellen, Quellenpositionen und manuelle Sprecherfassungen getrennt geprüft. Audit markiert Grafiken und andere redaktionell zu prüfende Elemente. | PASS |
| A04 | Redaktionelle Übertragung visueller Information | Sinn, Vollständigkeit und Vorlesbarkeit von Bildern, Grafiken, Tabellen und mathematischer Notation benötigen eine redaktionelle Prüfung des konkreten Buchs. Die Mapping-Abdeckung misst extrahierten Text. | BLOCKED für ein konkretes Produktionsbuch |
| B01 | NarrationPlan und Aussprache | `test_narration_plan.py` und `test_german_speech_normalizer.py` prüfen deterministische Reihenfolge, Kapitel-/Unit-IDs, Quellenabdeckung, Zahlen, Uhrzeiten, Datum, Prozent, Währung, Abkürzungen, URL, ISBN, technische Kennungen, Glossarpriorität, Schutzbereiche und unveränderte Originale. | PASS |
| B02 | Versionsgrenzen, Fingerprints und Kompatibilität | Eingefrorene Prompts werden beim echten Queue-Lauf mit den beobachteten Syntheseeingaben verglichen. Bestandsregressionen prüfen Legacy/v2, geänderten Buchtext, Glossar, Modellidentität, Audio-Konfiguration, falschen Fingerprint und manipulierte WAV-Bytes. | PASS |
| B03 | Vorschau und Produktion | Neutrale Hörprobe und Produktionspfad teilen den Normalisierungsdispatcher, durch `test_v3_preview_production_share_one_normalizer_and_fingerprints` geprüft. | PASS |
| B04 | Buchbezogene Hörprobe | Die produktionsidentische Buchvorschau aus Arbeitspaket B04 ist auf dem geprüften Stand offen. | NOT APPLICABLE |
| C01 | EPUB → Project V5 → NarrationPlan → WAV → M4B | `test_epub_to_m4b_after_abrupt_server_restart` nutzt echte HTTP-Routen, SQLite-Queue, WAV-Validierung und die produktive FFmpeg-Bibliothek. Der Download wird vollständig decodiert. AAC, Titel, Kanalzahl, Rate, Dauer, Kapitelgrenzen und Reihenfolge der decodierten Töne werden geprüft. | PASS |
| C02 | Satz-, Absatz- und Kapitelpausen | Plan-Regressionen prüfen symbolische Grenzen. Der Ende-zu-Ende-Test prüft drei Kapitelgrenzen und fehlende feste Pausendauern. Physische Zusatzpausen und erweitertes Mastering gehören zu den offenen Arbeitspaketen C01/C02. | PASS für Planmetadaten, NOT APPLICABLE für feste Pausen |
| D01 | Echter lokaler Dienstneustart und Resume | Erster Uvicorn-Prozess während des fünften Segments nach vier bestätigten Einheiten hart beendet. Zweiter Prozess mit neuer Instanz-ID und identischer Datenwurzel. Geprüft wurden Wiederverwendung des gültigen WAV, Regeneration nach falscher JSON-Form, defektem Header und fehlender Datei, Bereinigung des Teil-WAV, lückenlose Segmentliste und vollständiger Export. | PASS |
| D02 | Unterbrechung, Wiederholung und Konkurrenz | `test_studio_production.py` und `test_narration_plan.py` prüfen Abbruch in laufender Inferenz, Pause/Resume, Doppelauftrag, Lösch- und Exportkonflikte, beschädigte bzw. ausgetauschte WAVs, Versionsabweichungen und gesperrte Downloads vor Fertigstellung. | PASS |
| E01 | Fehlerhafte EPUB-Eingaben | `test_studio_api.py` prüft kaputtes ZIP, MIME, Traversal, Upload-, Archiv- und Entpackgrenzen, verdächtiges Kompressionsverhältnis, fehlende Spine-Ressource, defektes OPF-XML und leere Kapitel. Testläufe prüfen, dass weder ein Projekt noch ein Teilordner zurückbleibt. Kein Host-Stresstest. | PASS |
| E02 | WAV-, Metadaten-, Encoder- und Ressourcenfehler | Teil-WAVs, kaputte JSON-Metadaten, simulierter Datenträger-/Arbeitsspeichermangel und kontrollierter CUDA-OOM-Fehler am Inferenzübergang. Echte AAC-Datei vor simuliertem `ENOSPC` beim atomaren Umbenennen. API-Fehler, Rollback des Status, Teilbereinigung und Wiederholung geprüft. | PASS |
| E03 | Autorisierung und Daten in Fehlermeldungen | Fehlendes/falsches Bearer-Token, fremde Projekt-/Auftragskombination, Traversal und Download-Sperren. Testmarkierungen in Worker-/Dateisystemfehlern bleiben aus Job-API, Exportantwort und Studio-Fehlerlog. Historisch gespeicherte Rohfehler werden bei Detail- und Listenabfragen gefiltert. Linux-Symlink-Fälle sind separat erfasst. | PASS im geprüften Umfang |
| E04 | Erweiterte Sicherheitsabdeckung | Vollständiger Host-Datenträger, Host-OOM, systematisches XHTML-Fuzzing und Überprüfung von NTFS-ACLs. | NOT RUN |
| F01 | Gebautes React-Frontend und echte HTTP-Routen | `npm run build` und `tests/web/browser_smoke.py` mit Chromium und Uvicorn. Login, echter Upload, Kapitelauswahl, Speicherung, Glossar und mobile Darstellung. Im Smoke werden unter anderem bestimmte Fehler-, Download- und Wiederholungsantworten auf Browser-Ebene simuliert. | PASS |
| F02 | Produktionsstatus, Stimme, Export und Download | Echter lokaler HTTP-E2E prüft Queue, Aktivität, Recovery, Export und authentifizierten Download. FastAPI-TestClient deckt weitere Zustands- und Voice-Gates ab. Synthese und Modellverfügbarkeit sind Test-Doubles. | PASS |
| G01 | Syntax, Hashes und Konfigurationsdiagnose | `bash -n` einzeln für alle 14 Skripte, zwei `--help`-Pfade, sieben geprüfte SHA-256-Einträge und `doctor.py --json`. Die Diagnose meldet den fehlenden produktiven Mount und fehlende Qwen-/MOSS-Umgebungen im temporären Datenpfad. | PASS für Syntax, Hashes und Diagnoseformat |
| G02 | Gepinnte Revisionen, Manifest und Upgrade/Rollback | `tests/runpod` prüft Modellpins, Manifestintegrität, Fehlerfälle, simulierte CUDA-Preflights und Release-Transaktionen. Windows: 64 erfolgreich, 6 Assertion-Fehler durch POSIX-Pfadannahmen, 9 Fehler durch fehlende Symlink-Rechte bzw. nicht ausführbare POSIX-Skripte. Das Linux-Ergebnis ist am PR-Head über den Workflow **RunPod setup static tests** nachprüfbar. | PASS für 64 Windows-Fälle, BLOCKED lokal für 15 POSIX-Fälle |
| G03 | Basis- und PR-CI | Basis-SHA mit `gh run list --commit` und `gh run view` verifiziert. Die Abnahme vor dem Review auf `9812c8b536b8e8ad2060745c68211837c4400ac1` umfasste 337 Pytest-Fälle und fünf erfolgreiche Studio-Jobs sowie 79 erfolgreiche RunPod-Fälle. Für den Folgestand gelten die Checks von PR #32. | PASS für Basis und ursprünglichen Abnahmecommit |

Der Audio-Probelauf ergab 28 WAV-Dateien à 7.680 Samples mit 24.000 Hz, mono, PCM16. Der AAC-Download ergab 215.040 decodierte Samples und 8,960 Sekunden. Die drei Marker liefen von 0,000 bis 0,960 s, 0,960 bis 6,720 s und 6,720 bis 8,960 s. Der Test erlaubt für die Gesamtdauer höchstens einen AAC-Frame (1.024 Samples) und für Marker höchstens 1 ms Abweichung. Die Frequenzprüfung im Zentrum jedes Segments belegt die Verkettungsreihenfolge.

## Reproduzierte Probleme und Korrekturen

| Befund | Reproduktion und Ursache | Auswirkung und Korrektur | Regression |
|---|---|---|---|
| Gültiges JSON im falschen Format stoppt Resume | Nach vier bestätigten Segmenten den zweiten Checkpoint durch `[]` ersetzen und den Prozess neu starten. Die Leser riefen `.get` auf einem Array auf. | Ein defekter Checkpoint konnte den gesamten Auftrag bzw. Export und Qualitätsbericht abbrechen. Checkpoint-Leser verlangen ein JSON-Objekt. Resume regeneriert, Export weist ungültige Checkpoints kontrolliert zurück und der Qualitätsbericht kennzeichnet sie als ungeprüft. | Vorher FAIL, danach PASS. Restart-E2E, `test_export_rejects_missing_or_wrong_plan_checkpoint` und Qualitätsbericht. |
| Worker-Fehler geben interne Inhalte preis | Einen Marker mit synthetischem Buchtext in OSError, MemoryError oder simuliertem CUDA-OOM werfen. `str(exc)` ging direkt in Job-API und die Exception in das Studio-Log. | API und Aktivitäten nutzen kontrollierte Meldungen. Das Studio-Log enthält Job-ID und Fehlertyp. Alte Rohfehler werden auf Leseebene gefiltert. Nach erfolgreichem Resume wird der Fehlerstatus geleert. | Drei Fehlerparameter vorher FAIL, danach PASS. Test für alte Fehler in Detail- und Listenabfragen ergänzt. |
| Kurzer WAV-Header konnte Retry umgehen | Der Backend-Doppelgänger liefert nur `RIFF`. Das Python-WAV-Modul kann einen `EOFError` statt `wave.Error` auslösen. | Ein abgebrochener Header wird jetzt als korrupter Audio-Checkpoint bzw. wiederholbarer Synthesefehler behandelt. | PASS. Der neue Fall prüft drei Versuche, Teil-WAV-Bereinigung und anschließende Wiederaufnahme. |
| AAC-Schreibfehler führt zu HTTP 500 | Nach echter AAC-Kodierung beim finalen Umbenennen `ENOSPC` auslösen. `AudioWriteError` fiel außerhalb der bisher abgefangenen API-Typen. | Export gibt einen kontrollierten HTTP 409 ohne Dateipfad zurück. Der Auftrag bleibt für einen erneuten Export verfügbar, das Teil-M4B wird entfernt und der Download bleibt bis zum erfolgreichen Export gesperrt. | Vorher HTTP 500, danach PASS im E2E mit anschließender erfolgreicher Codierung und Decodierung. |
| Shell-Syntaxgate prüfte nur das erste Skript | `bash -n script1 script2 ...` behandelt die weiteren Namen als Positionsargumente. | Der RunPod-Workflow prüft jedes Skript einzeln. | PASS im lokalen Git Bash für alle 14 Dateien. |
| EPUB-Testfixture fehlte EPUB-3-Modifikationszeitpunkt | Das OPF-Metadatenfragment enthielt `dcterms:modified` nicht. | Der feste Zeitstempel wurde im synthetischen Generator ergänzt. | PASS für ZIP-Struktur, Import, API und Browser. |

## Ausgeführte Befehle und Ergebnisse der ursprünglichen Abnahme

Die Befehle verwenden im Folgenden `python` aus dem isolierten Venv. Unter Windows wurden `--basetemp` und die Logs in ein auftragsspezifisches temporäres Verzeichnis umgeleitet. Vor dem Lesen von Workspace-Dateien lief der vorgeschriebene SonarQube-Geheimnis-Scan.

```bash
python -m pytest -q -rs -p no:cacheprovider --confcutdir=tests/web tests/web
python -m pytest -q -rs -p no:cacheprovider \
  tests/text/test_epub_extractor.py tests/text/test_epub_css.py \
  tests/text/test_epub_content_audit.py tests/text/test_epub_section_skip_detector.py \
  tests/text/test_epub_private_regressions.py tests/text/test_german_speech_normalizer.py \
  tests/text/test_phrase_grouper.py tests/text/test_phrase_segmenter.py \
  tests/project/test_book_serialization.py tests/project/test_project_book_integration.py \
  tests/project/test_json_save_util.py tests/audio/test_m4b_chapter_util.py \
  tests/audio/test_audio_writers.py
npm --prefix web ci --no-audit --no-fund
npm --prefix web run build
PYTHONPATH=. python tests/web/browser_smoke.py
npm run test:browser
for script in runpod/*.sh; do bash -n "$script" || exit; done
bash runpod/setup.sh --help
bash runpod/start.sh --help
sha256sum -c runpod/locks/SHA256SUMS
python -m unittest discover -s tests/runpod -p 'test_*.py' -v
python runpod/doctor.py --json
```

| Lokaler Lauf | Bestanden | Fehlgeschlagen | Übersprungen | Beleg |
|---|---:|---:|---:|---|
| Studio-Web inklusive neuer Regressionen | 111 | 0 | 0 | `web-release.log` |
| Text, Projekt und Audio | 412 | 0 | 4 | `text-audio.log`. Ein Symlink-Fall, zwei `/dev/full`-Fälle und ein `/proc/self/fd`-Fall sind Windows-bedingt übersprungen. |
| Browser-Player-JavaScript | 27 | 0 | 0 | `browser-player.log` |
| RunPod-Offline-Regression auf Windows | 64 | 15 | 0 | `runpod-static.log`. 6 Assertions und 9 Laufzeitfehler, jeweils auf die Plattform zurückgeführt. |
| **Summe lokaler automatisierter Testfälle** | **614** | **15** | **4** | Linux-CI, Build, Chromium-Smoke, Syntax und Manifestprüfung werden separat ausgewiesen. |

TypeScript/Vite-Build, Chromium-Smoke, 14 Shell-Syntaxprüfungen und sieben Hashprüfungen waren zusätzlich erfolgreich. Die fünf reproduzierten roten Studio-Testausführungen vor den Korrekturen sind in `red.log` und `red-export.log` belegt und gehören nicht zur abschließenden Testsumme. Lokal waren 15 native RunPod-Fälle und die vier genannten Linux-/Symlink-Regressionen umgebungsbedingt blockiert. Die Matrix weist zusätzlich die redaktionelle Prüfung des Produktionsbuchs als blockiert aus. FastAPI-/Starlette- und EbookLib-Deprecation-Warnungen waren sichtbar.

### Wiederholung auf Linux und Windows

Die Linux-Referenzumgebung und exakten Installationsschritte stehen in [German Audiobook Studio CI](../.github/workflows/german-audiobook-studio.yml). Die RunPod-Regression läuft separat in [RunPod Static CI](../.github/workflows/runpod-setup.yml).

Unter Windows liefen die Python-Installationen im Venv, jeweils aus getrennten Paketindizes. Die Zusatzpakete sind Windows- und Torch-Abhängigkeiten außerhalb der Linux-Locks.

```powershell
python -m pip install --no-deps --require-hashes -r web/requirements-runtime.lock.txt
python -m pip install --no-deps --require-hashes -r web/torch-cpu.lock.txt
python -m pip install pytest==8.4.2 playwright==1.56.0 pywin32==312 colorama==0.4.6 win32-setctime==1.2.0 jinja2==3.1.6 networkx==3.5 sympy==1.14.0
python -m pip check
$env:PLAYWRIGHT_BROWSERS_PATH = '<isolierter Testordner>/browsers'
python -m playwright install chromium --only-shell
$env:CUDA_VISIBLE_DEVICES = ''
$env:HF_HUB_OFFLINE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = '.;<isolierter Testordner>/windows-compat'
```

Im temporären `windows-compat`-Verzeichnis lag folgende `fcntl.py`. Sie adaptiert ausschließlich die für den Test benötigte Sperroperation. Die Windows-Tests müssen dabei mit denselben Umgebungsvariablen wie die gestarteten Testserver laufen.

```python
import errno
import msvcrt
import os

LOCK_EX = 2
LOCK_NB = 4


def flock(fd, flags):
    if flags != LOCK_EX | LOCK_NB:
        raise NotImplementedError
    if not isinstance(fd, int):
        fd = fd.fileno()
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno != errno.EACCES:
            raise
        raise BlockingIOError(exc.errno, "Studio test lock is held") from exc
```

## Abschlussreview von PR #32

Der fokussierte Vorher-Lauf auf Basis von `9812c8b` ergab vier reproduzierte Fehlschläge und einen bestandenen Legacy-Export. Nach den Korrekturen bestanden alle 40 gezielt gewählten Produktions-, Export-, Recovery- und NarrationPlan-Fälle. Der Neustart-E2E bestand erneut mit der zusätzlichen Assertion, dass der AAC-Writer den simulierten Speicherfehler erreicht. `python -m testx.no_ffmpeg_smoke --skip-startup` bestand alle 20 Audio-I/O-Prüfungen und die Herkunftsprüfung der geladenen FFmpeg-Bibliotheken. Diese Läufe sind separat von den historischen Windows-Zahlen oben zu lesen.

| Befund | Reproduktion und Korrektur |
|---|---|
| Fehler beim Aktivitätsprotokoll blockiert den Export | `ENOSPC` beim atomaren Speichern von `activity.json` ließ den Auftrag in `exporting`. Ein Schreibfehler des Diagnoseprotokolls unterbricht den Auftrag jetzt nicht mehr. Die Warnung enthält Auftrags-ID und Fehlernummer. Der E2E-Test injiziert den Fehler zusammen mit einem separaten AAC-Schreibfehler und prüft anschließenden Export und Download. |
| Legacy-Aufträge können bei fehlenden Segmenten oder leeren WAVs unvollständig fertigmelden | Einen historischen Zwei-Segment-Auftrag mit einem Eintrag im Manifest oder mit einem WAV ohne Samples exportieren. Resume und Export prüfen die Segmentzahl gegen den gespeicherten Auftrag. Der Export lehnt leere WAVs ab. Der unveränderte historische Export mit zwei gültigen WAVs bleibt als Positivfall bestehen. |
| Ein interner Export-`RuntimeError` gab Rohtext an den Browser weiter | Ein synthetischer privater Marker im Fehler bei der Kapitelverarbeitung erschien im HTTP-409-Detail. Der Fehlerpfad gibt jetzt eine feste Meldung zurück, schreibt nur den Fehlertyp ins Studio-Log und setzt den Auftrag für einen erneuten Export zurück. |
| Die Testfrequenz war nicht eindeutig | Die bisherige Modulo-Zuordnung erlaubte gleiche Frequenzen für verschiedene Prompts. Der E2E-Test codiert jetzt den Index jeder Einheit als eigene Frequenz. Er vergleicht die Synthese-Prompts weiterhin separat mit dem Plan. |

Belege zur ursprünglichen Abnahme stehen im [Studio-Lauf 42434362561](https://github.com/thomasasen/tts-audiobook-tool/actions/runs/42434362561) und im [RunPod-Lauf 42434362586](https://github.com/thomasasen/tts-audiobook-tool/actions/runs/42434362586). Der PR beschreibt den geprüften Review-Head und verlinkt seine CI-Läufe. [Pull Request #32](https://github.com/thomasasen/tts-audiobook-tool/pull/32)

## E03-B · Offene Release-Nachweise

| ID | Erforderlicher Nachweis | Status |
|---|---|---|
| B-GPU-01 | Reale CUDA-Inferenz auf dem Zielhost | BLOCKED |
| B-GPU-02 | Tatsächliches Laden des gepinnten Thorsten-Modells | BLOCKED |
| B-GPU-03 | Deutsche Sprecher- und Hörfreigabe | BLOCKED |
| B-GPU-04 | Vollständige GPU-basierte EPUB→M4B-Produktion | BLOCKED |
| B-GPU-05 | Echter RunPod-Neustart mit Resume auf persistentem Volume | BLOCKED |
| B-GPU-06 | Mindestens 20 Minuten dokumentierte menschliche Hörkontrolle | BLOCKED |
| B-GPU-07 | Gemessener reiner Inferenz-RTF | BLOCKED |
| B-GPU-08 | Gemessener GPU-/VRAM-Spitzenverbrauch | BLOCKED |
| B-GPU-09 | Tatsächliche Kosten pro Audiostunde | BLOCKED |
| B-GPU-10 | Abschließende Produktionsfreigabe | BLOCKED |

Für den Zielbetrieb stehen außerdem reale Installation, Live-Upgrade/Rollback sowie GPU- und Volume-Fehler unter kontrollierten Bedingungen aus. Durchführung und Messdefinitionen stehen in [RunPod Acceptance](../runpod/ACCEPTANCE.md) und [Voice Evaluation](../runpod/VOICE_EVALUATION.md).
