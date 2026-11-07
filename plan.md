# plan.md — Red-Team-Roadmap für hochwertige deutsche Fachhörbücher

> Stand: 07.11.2026 · Repository: https://github.com/thomasasen/tts-audiobook-tool · Ausgangsprüfung: master bei 5e2979dc86aa133c25a92862d4caf41907860429
>
> **Dies ist ein Umsetzungsplan, kein Nachweis einer erfolgreichen Umsetzung.** Alle Aufgaben starten offen. Vor jeder Bearbeitung die tatsächliche aktuelle Codebasis, PRs und CI erneut prüfen. Die bisher fehlende reale GPU-/Hörbuch-Abnahme bleibt ein Release-Blocker.

## 0. Zielbild und unverhandelbare Produktvorgaben

**Ziel:** Aus deutschen, rechtmäßig verarbeiteten EPUB-Fachbüchern reproduzierbar natürlich klingende, texttreue, korrekt strukturierte und zuverlässig exportierbare M4B-Hörbücher erstellen. Qualitätsgewinn hat Vorrang vor zusätzlichen Bedienelementen oder theoretisch unterstützten Modellen.

- German-first: deutsche Standardsprache, natürliche Betonung, sinnvoll gesprochene Zahlen und englische Fachbegriffe; keine US-Akzent-Fallbackstimme.
- Nur **fertige, rechtlich freigegebene Sprecherprofile** im Webstudio. Kein Stimmklonen durch Nutzer, kein Upload von Sprecherreferenzen, kein Voice-Design-Editor. Unterschiedliche Presets derselben Person dürfen nicht als verschiedene Sprecher erscheinen.
- Sprechstil-Prompts nur bei einem tatsächlich kompatiblen Modell-/Inferenzpfad, **und nur wenn sie bei Hörprobe und Produktion identisch wirken**. Für Thorstens aktuelles CosyVoice3-SFT ist freie Promptsteuerung nicht belegt; keine Scheinregler.
- Original-EPUB und Originaltext unverändert archivieren; Normalisierungen, Glossar und Hörfassung strikt davon trennen. Keine stillschweigenden Auslassungen oder inhaltlichen Umschreibungen.
- Die bestehende EPUB-/Project-V5-/book.v3-Engine, Segmentierungs-/Audiofunktionen und TTS-Adapter **wiederverwenden**; keinen zweiten EPUB-Parser oder unverbundene Parallelpipeline einführen.
- Bestehende TUI und andere Modellpfade erhalten; RunPod-Webstudio bleibt geschützt, Single-User und Single-GPU, bis eine bewusst abgenommene Architekturänderung erfolgt.
- Keine privaten EPUBs, generierten Audiodateien, Stimmreferenzen, Tokens oder proprietären Texte im öffentlichen Git, in CI-Artefakten oder ungeschützten Logs.
- Technische CI, GPU-Lauffähigkeit, menschliche Hörabnahme und Release-Freigabe sind **vier unterschiedliche Nachweise**. Nie einen davon durch einen anderen ersetzen.

## 1. Arbeitsvertrag für GPT/Codex

1. Vor dem ersten PR: AGENTS.md, docs-dev/test-routing.md, runpod/RELEASE_STATUS_2026-11-07.md, runpod/ACCEPTANCE.md, docs-dev/epub-import-flow.md, docs-dev/text-segmentation.md, docs-dev/tts-model-architecture.md und die jeweils betroffenen Source-/Testdateien lesen. Dokumentationsaussagen mit aktuellem Code und GitHub-Status abgleichen.
2. Den neuesten Stand von master sowie offene/gestapelte PRs und bereits erfüllte Aufgaben ermitteln. Die SHA oben ist nur die historische Prüfgrundlage. Doppelte Arbeit vermeiden und bestehende Implementierungen nicht zurückbauen.
3. **Immer das erste noch offene, nicht blockierte Arbeitspaket** aus Abschnitt 3 auswählen. Abhängigkeiten prüfen. Kleine Änderungen auf einem eigenen Feature-Branch vom aktuellen master; wenn eine offene Vorarbeit nötig ist, gestapelte Abhängigkeit klar im PR benennen. Keine konkurrierenden großen PRs.
4. Vor der Codeänderung kurz Ursache, gewünschte Architektur, betroffene Dateien, Rückwärtskompatibilität, Tests und Rückfallstrategie festhalten. Bestehende Project-V5-Projekte, Jobs, WAV-Checkpoints und M4B-Dateien nicht leichtfertig invalidieren.
5. Implementierung + gezielte Regressionstests + nötige Verbraucher-/Browser-Tests aus einem Guss. Den Testumfang nach docs-dev/test-routing.md wählen. Für reine Dokumentation keine teure Vollsuite. Skips und Umgebungsgrenzen explizit nennen.
6. Jedes Paket erhält eine kleine PR mit Aufgaben-ID im Titel, Vorher-/Nachher-Befund, Testbelegen und verbleibenden Risiken. Kein ungetesteter Merge. Ohne ausdrückliche Freigabe keine Änderungen nach master mergen.
7. Beim **nachgewiesenen** Abschluss die Aufgabe hier abhaken, PR-/Commit-Link, ausgeführte Testbefehle und Status eintragen. Ein nur vorgeschlagener oder nur teilweise implementierter Punkt bleibt offen. Wenn der Plan noch auf einem nicht gemergten PR-Branch liegt, den Fortschritt dort pflegen und nach Integration abgleichen.
8. Bei fehlender echter GPU, RunPod-Zugriffsmöglichkeit oder menschlicher Hörabnahme: offline umsetzbare Teile fertigstellen, die entsprechenden Gate-Aufgaben als **BLOCKIERT mit konkretem Grund** dokumentieren. Keine erfundenen Hörnoten, RTF-, VRAM-, Kosten- oder Audioqualitätswerte.
9. Nach jedem Arbeitspaket eine knappe Übergabe: erledigt, PR/Commit, Tests/CI exakt für die Head-SHA, offene Risiken, nächstes Paket. Unklare Anforderungen nur dann rückfragen, wenn sie eine fachliche Entscheidung oder irreversible Änderung erzwingen.

### Definition of Done für jedes Arbeitspaket

- [ ] Angestrebtes Verhalten und Gegenfälle durch Tests belegt; keine stillen Regressionen.
- [ ] Privatsphäre, Berechtigungen, persistente Daten und Neustart berücksichtigt.
- [ ] Dokumentation und vorhandene öffentliche UI-Aussagen stimmen mit dem Code überein.
- [ ] Gezielte Tests und CI am **aktuellen** Commit grün; übrige Blocker transparent.
- [ ] Rückwärtskompatibilität geprüft oder ausdrücklich begrenzte Migration samt Test dokumentiert.
- [ ] PR/Commit und Evidenz im Fortschrittsprotokoll erfasst.

## 2. Red-Team-Ausgangsbefunde (vor Umsetzung gegen HEAD validieren)

| ID | Evidenz aus dem statischen Review | Risiko | Priorität |
|---|---|---|---|
| RT-01 | production._audio_quality importiert faster_whisper; start-web.sh setzt standardmäßig ASR=small; web/requirements-runtime.txt enthält keine faster-whisper-Abhängigkeit. | Auf sauberem Web-Runtime-Setup ist automatische Sprachprüfung voraussichtlich nicht verfügbar. | P0 Codequalität |
| RT-02 | production._manifest enthält Kapitelindex und Titel; export_job erkennt Kapitelwechsel anhand des Titels. | Zwei direkt folgende Abschnitte mit identischem Titel können zu einer Kapitelmarke zusammenfallen. | P0 Funktion |
| RT-03 | production._manifest fügt Phrase-Texte zusammen; export_job schreibt die WAVs direkt nacheinander. | Semantische Satz-/Absatz-/Kapitelpausen und Sprechregie sind in diesem Produktionspfad nicht explizit abgebildet. | P1 Hörqualität |
| RT-04 | SAMPLE_SENTENCES und SAMPLE_LENGTHS liefern feste Sprecherproben statt tatsächlichen Buchtext. | Die Freigabe prüft Fachbegriffe und Rhythmus des Zielbuchs nicht. | P1 Hörqualität |
| RT-05 | PromptNormalizer + _normalize_de decken deutsches Fachvokabular, zusammengesetzte Zahlen-/Währungsformate und Akronyme nicht ausdrücklich systematisch ab. | Falsche Zahlenaussprache und unstete Anglizismen möglich. | P1 Texttreue |
| RT-06 | Webstudio nutzt festen Thorsten-CosyVoice3-SFT-Pfad, während die ältere Engine auch Qwen3-TTS und MOSS kennt. | Vorhandene, modellabhängige Möglichkeiten bleiben ungenutzt; Qualitätsrangfolge unbekannt. | P1 Modell |
| RT-07 | Web-M4B-Export übergibt derzeit primär den Titel; das Frontend lädt große Audiodateien als vollständige Blobs. | Unvollständige Buchmetadaten und hoher Browser-Speicherverbrauch. | P2 Export/UX |
| RT-08 | RunPod-Start folgt master; Modellgewichte haben lokale Integritäts-Hashes, aber kein vollständig festgeschriebenes Upstream-Revision-/Release-Schema. | Identischer Neustart muss nicht identischen Software-/Modellstand liefern. | P1 Betrieb |
| RT-09 | runpod/RELEASE_STATUS_2026-11-07.md dokumentiert keine reale GPU-/Langform-/Neustart-Abnahme. | Die natürliche Qualität und Ende-zu-Ende-Produktionsreife bleiben unbelegt. | **Release-Blocker** |

**Wichtig:** RT-01 und RT-02 sind konkrete statische Codebefunde, aber keine behaupteten Live-Fehlerreproduktionen. RT-03 bis RT-08 sind verifizierungsbedürftige Funktionseinschränkungen bzw. Architektur-/Qualitätsrisiken. RT-09 ist eine dokumentierte Evidenzlücke.

## 3. Priorisierte Reihenfolge und Abhängigkeiten

Eine Zeile ist ein eigenständig prüfbares PR-Arbeitspaket; nur nachgewiesene Abschlüsse abhaken.

| Erledigt | Paket | Priorität | Voraussetzungen | Umfang | Ziel |
|---|---|---|---|---|---|
| [ ] | A01 | P0 | keine | S | ASR-Installation und ehrliche Betriebsanzeige |
| [x] | A02 | P0 | keine | S | Kapitel-IDs statt Titel für M4B-Marker |
| [ ] | A03 | P1 | keine | M | Reproduzierbare Releases, Abhängigkeiten und Modelle |
| [ ] | A04 | P2 | keine | M | Speicherfreundliche, authentifizierte Downloads |
| [ ] | B01 | P1 | keine | M | EPUB-Struktur-/Auslassungs-Audit und Vorschau |
| [ ] | B02 | P1 | B01 | M | Deutsche Textnormalisierung/Fachbegriff-Layer |
| [ ] | B03 | P1 | A02, B01, B02 | L | Versionierter NarrationPlan mit Text-/Sprechmetadaten |
| [ ] | B04 | P1 | B02, B03 | M | Buchbezogene, produktionsidentische Hörproben |
| [ ] | C01 | P1 | B03 | M | Natürliche Satz-/Absatz-/Kapitelübergänge |
| [ ] | C02 | P1 | A02, C01 | M | Audio-Mastering, Metadaten, Exportvalidierung |
| [ ] | D01 | P1 | B03, B04 | L | Wiederverwendbare modellabhängige TTS-Adapter |
| [ ] | D02 | P1 | D01 | M + GPU/Hörtest | Blindvergleich Thorsten/Qwen3/MOSS |
| [ ] | D03 | P2 | D02 | M | Belegte Modellauswahl und optionale echte Stilfähigkeit |
| [ ] | E01 | P2 | stabile UI-Verträge aus B04/C02 | M | React-Refactoring und Barrierefreiheit |
| [ ] | E02 | P1 | A01–C02 | M | Negativ-/Sicherheits-/Recovery-Regressionen |
| [ ] | E03 | **P0 Release** | alle release-relevanten Pakete | GPU + menschliche Abnahme | Echtes EPUB→M4B und Freigabeentscheidung |

S/M/L sind relative Implementierungsgrößen, **keine Zeitversprechen**. A03, A04 und B01 können unabhängig laufen, aber PRs nicht unkontrolliert parallel ausführen. D02 und E03 können ohne geeignete GPU/Operator nicht als erledigt markiert werden.

## 4. Präzise Arbeitspakete

### A01 — Tatsächlich funktionierende deutsche ASR-Qualitätsprüfung

**Fundstellen:** web/requirements-runtime.txt, runpod/install-web.sh, runpod/start-web.sh, tts_audiobook_tool/web_studio/production.py, tests/web/, .github/workflows/german-audiobook-studio.yml.

- [ ] Frischen Web-Venv-Pfad und tatsächliche faster-whisper-Verfügbarkeit nachweisen; direkt und transitiv installierte Abhängigkeiten unterscheiden.
- [ ] Eine geprüfte und kompatible faster-whisper-Version in die **getestete** Runtime aufnehmen oder ASR bewusst als optionales installierbares Extra definieren. In jedem Fall darf der Standardstart nicht stillschweigend eine fehlende Funktion versprechen.
- [ ] Readiness/Capabilities: ASR aktiv, deaktiviert, fehlt, Modell nicht geladen, Verarbeitung fehlgeschlagen unterscheiden. Keine Buchtexte, vollständigen Transkripte oder internen Modellpfade in Browserfehlern/öffentlichen Logs.
- [ ] Einen echten kurzen CPU-ASR-Test mit selbst erzeugtem/zulässig bereitgestelltem deutschem Audio und Negativfall ausführen; reine Import-Mocks reichen nicht. CI-Downloadaufwand begrenzen; bei zu hohen Kosten separaten Integrationsjob definieren und kennzeichnen.
- [ ] Wiederholungen, Auslassungen und Zahlabweichungen nicht ausschließlich über difflib.SequenceMatcher-Schwelle 0,75 behandeln. Normalisierte WER/CER bzw. ausgerichtete Verdachtsstellen ergänzen, ohne ASR-Fehler automatisch als TTS-Fehler einzustufen.
- [ ] UI meldet explizit, wenn eine Prüfung **nicht durchgeführt** wurde.

**Akzeptanz:** Frisches Web-Setup kann ASR real ausführen oder zeigt eindeutig „nicht installiert/deaktiviert“. Keine falschen „geprüft“-Zähler. Positiv-/Negativfälle getestet.

### A02 — Stabile Kapitelidentität und korrektes M4B-TOC

**Fundstellen:** tts_audiobook_tool/web_studio/production.py (_manifest, export_job), tests/web/test_studio_export.py, tests/audio/test_m4b_chapter_util.py.

- [ ] Ein vorhandenes stabiles Kapitelindex-/ID-Feld beim Export verwenden; nie allein den Titel als Identitätsmerkmal.
- [ ] Identische aufeinanderfolgende Kapiteltitel, leere Titel, mehrfach benannte Überschriften, Start ab Vorwort und ausgeschlossene Kapitel testen.
- [ ] Kapitelstart/-ende, Reihenfolge, Audiodauer und AAC-Decodierbarkeit prüfen; keine neue Project-V5-Schema-Version erzwingen, falls nicht nötig.

**Akzeptanz:** Zwei aufeinanderfolgende Kapitel namens „Einleitung“ bleiben zwei Marker; kein Segment wird ausgelassen oder doppelt aufgenommen.

### A03 — Reproduzierbare, rücksetzbare RunPod-/Modell-Releases

**Fundstellen:** runpod/autostart.sh, runpod/install-thorsten.sh, runpod/prefetch-thorsten.sh, runpod/install-web.sh, requirements-*.txt, web/package.json, README.md und RunPod-Dokumentation.

- [ ] Entwicklungsmodus (bewusst aktueller master) von **Release-Modus mit festem Git-Commit** trennen; Checkout, Rollback und Neustart deterministisch.
- [ ] CosyVoice-Framework, Thorsten-Finetuning, Basis-Modell und alle späteren Modelle mit belegbaren vollständigen Revisionen pinnen. Lokale SHA-256-Manifeste bleiben Integritätstest, ersetzen aber keinen Upstream-Vertrauensanker.
- [ ] Relevante Python-/Node-Abhängigkeiten passend zu getrennten Venvs reproduzierbar sperren; Installationspfade/Versionswechsel idempotent und fail-closed prüfen.
- [ ] Keine unkontrollierten Updates beim Pod-Neustart; klarer Upgrade-/Rollback-Befehl, dokumentierte Mindestanforderungen, optionales Modell nur bewusst laden.
- [ ] Testfälle für gültigen Cache, defekte Gewichte, Versionswechsel, fehlenden Mount, CUDA-Fehler und Speichermangel.

**Akzeptanz:** Derselbe Release-Stand installiert nach Neustart dieselben Artefakte; beschädigte/abweichende Modelle werden nicht als freigegeben dargestellt.

### A04 — M4B-Download ohne vollständiges Speicherpuffern

**Fundstellen:** web/src/studio.tsx, tts_audiobook_tool/web_studio/app.py.

- [ ] Bestehende Antwort-/Dateistream- und Range-Fähigkeiten prüfen. Authentifizierung und Downloadberechtigung behalten.
- [ ] Auf unterstützten Browsern die Datei stückweise auf ein vom Nutzer gewähltes Ziel streamen (z. B. File System Access API); für nicht unterstützte Browser einen klar begrenzten, beschriebenen Fallback wählen.
- [ ] Keine dauerhaft gültigen Token in URL oder Querystring, keine ungeschützten statischen M4B-Verzeichnisse.
- [ ] Abbruch, große Datei, fehlende Berechtigung, Browser-Fallback und unvollständigen Transfer testen.

**Akzeptanz:** Mehrstündige M4B-Dateien benötigen nicht zwingend einen entsprechend großen JS-Blob im RAM; ein fehlgeschlagener Download wird als solcher angezeigt.

### B01 — EPUB-Verlustfreiheit und nachvollziehbare Auslassungen

**Fundstellen:** tts_audiobook_tool/text_ops/epub_extractor.py, epub_section_skip_detector.py, tts_audiobook_tool/web_studio/app.py, tests/text/test_epub_*.py, tests/web/test_real_epub_e2e.py.

- [ ] EPUB-Spine, Navigationsanker, Unterabschnitte, Inline-Formatierung, Tabellen, Listen, Fußnoten, Bildunterschriften und literarische Trennelemente anhand selbst verfasster Test-EPUBs auditieren.
- [ ] Vom Parser automatisch ausgelassene Inhalts-/Metadatenabschnitte samt Grund und kleiner Vorschau sichtbar machen; falsche Auslassungen korrigierbar gestalten, ohne DRM oder Rechteprüfungen zu umgehen.
- [ ] Gemischte XHTML-Dokumente mit Navigation und Fließtext nicht komplett verwerfen. Lesetext, Vorwort, Kapitelreihenfolge und Zeichengrenzen explizit prüfen.
- [ ] Für schwer vorlesbare Tabellen/Formeln/Abbildungen eine **optionale** Sprecherfassung mit menschlicher Vorschau spezifizieren; nichts ungefragt erfinden oder ändern.
- [ ] Quelltext-zu-Sprechtext-Mapping und verlustfreie Segmentabdeckung vorbereiten; Originalprojekt unverändert lesbar.

**Akzeptanz:** Synthetische Testbücher und wenigstens ein privat getesteter komplexer EPUB-Typ haben vollständigen, erklärbaren Vorlesetext. Auslassungen sind sichtbar und überprüfbar.

### B02 — Deterministische deutsche Textnormalisierung

**Fundstellen:** tts_audiobook_tool/text_ops/prompt_normalizer.py, tts_audiobook_tool/web_studio/production.py (_normalize_de), tests/text/test_prompt_normalizer.py.

- [ ] Eine zentrale, testbare Vorlesetext-Transformation ergänzen; bestehende globale Normalisierung wiederverwenden, statt Regeln zu duplizieren.
- [ ] Mit Golden Tests abdecken: 1,25 / 1.234 / 49,90 €, 3,5 %, 2026, 08:30, Datumsformate, §§, URLs, E-Mail, Aufzählungsnummern, CRM/SaaS/MEDDPICC/ARR/NRR/ROI/SLA/EBITDA sowie deutsch-englische Wechsel.
- [ ] Mehrwort-Fachbegriffe, Groß-/Kleinschreibung, Interpunktion, Satzgrenzen und kontextspezifische Ausnahmen des Glossars prüfen.
- [ ] Bei Mehrdeutigkeit Originalform erhalten oder prüfbaren Warnhinweis liefern; keine automatische Fantasieaussprache.
- [ ] Jede geänderte Sprechform ist in Vorschau und Debug-Evidenz nachvollziehbar; Original-EPUB bleibt unverändert.
- [ ] Modellabhängige Sonderzeichen-/Steuer-Token-Regeln bleiben im jeweiligen Modelladapter.

**Akzeptanz:** Golden Cases vorhersagbar und stabil; keine Doppel-Normalisierung; Hörprobe und Produktion verwenden dieselbe Transformation.

### B03 — Versionierter, EPUB-bewusster NarrationPlan

**Fundstellen:** Project V5 / book.v3, PhraseGroup-/Reason-Strukturen, tts_audiobook_tool/web_studio/production.py (_manifest, Job-Plan), docs-dev/text-segmentation.md.

- [ ] Ein schlankes versioniertes Zwischenformat für **vorzulesende Einheiten** entwerfen: stabile Kapitel-ID, Reihenfolge, Original-Sprechspanne, normalisierter Prompt, Abschnittstyp (Fließtext/Überschrift/Liste usw.), Grenzgrund, geplante Pause und Modell-/Audio-Konfiguration.
- [ ] Bestehende Phrase-/Section-Metadaten wiederverwenden; keine zweite eigentliche EPUB-Interpretation.
- [ ] Mapping von originalen Textspannen zu tatsächlich gerenderten Einheiten und Abdeckungsprüfung einführen; Überschriften nur dann sprechen, wenn sie im gewählten Vorlesetext vorgesehen sind.
- [ ] Version/Hash in Cache- und Wiederaufnahme-Fingerprints aufnehmen; alte Jobs sicher weiterverwenden oder mit expliziter Migration/Neugenerierung behandeln.
- [ ] Das Format so schneiden, dass alle kompatiblen Backends denselben texttreuen Plan erhalten und nur Inferenzdetails adaptieren.

**Akzeptanz:** Deterministische Einheit-/Kapitelabdeckung, identisches Kapitel-Ordering, sichere Resume-Strategie, Tests für verlorene und doppelte Spannen.

### B04 — Buchbezogene Hörprobe mit Produktionsparität

**Fundstellen:** web/src/studio.tsx, web_studio/app.py, web_studio/production.py (SAMPLE_SENTENCES, make_sample).

- [ ] Die bestehende neutrale Standard-Stimmprobe behalten; zusätzlich **Vorschau aus dem gewählten Buch** mit Startstelle/Kapitel und den vorhandenen ca. 5/15/30/60-Sekundenoptionen.
- [ ] Aus zusammenhängenden, vollständigen Sätzen wählen; nie mitten im Wort abschneiden; Sprechtext aus demselben NarrationPlan, Glossar, Modell und Audio-Pfad wie die Produktion beziehen.
- [ ] Modell-/Konfigurations-Fingerprint für die Vorschau speichern. Sprecherfreigabe und projektbezogene Qualitätsprüfung als getrennte Konzepte behandeln; nach relevanten Änderungen muss die betroffene Qualitätsfreigabe erlöschen.
- [ ] Vorschau-Auftrag, Produktionsauftrag und GPU-Lock dürfen nicht kollidieren; Browser-Abspielbarkeit, echte Dauer und Neustart-/Fehlerzustände testen.

**Akzeptanz:** Fachbegriffe und Aussprache im Hörbeispiel entsprechen bei gleichen Einstellungen dem späteren Buch. Keine nur kosmetische Preview.

### C01 — Sprechregie, Pausen und artefaktfreie Segmentgrenzen

**Fundstellen:** B03-NarrationPlan, tests/audio/test_sentence_gap_util.py, tests/audio/test_reason_pauses.py, tts_audiobook_tool/sound/ und web_studio/production.py.

- [ ] Vorhandene Satzlücken-/Reason-Pause-DSP-Funktionen auf Wiederverwendung prüfen, bevor neuer Code geschrieben wird.
- [ ] Unterschiedliche Behandlung von Satzende, Absatz, Unterüberschrift, Aufzählung und Kapitelgrenze vorsehen. Vorgabewerte anhand Hörtests kalibrieren, nicht blind als fixe Universalwerte festlegen.
- [ ] Bereits vom TTS erzeugte Stille berücksichtigen, damit Pausen nicht kumulativ ausufern; kein Abschneiden von Konsonanten, Atemenden oder letzten Silben.
- [ ] Bei Bedarf kleine, nachweislich unhörbare Fades an echten Schnittstellen und sanfte Pegelkontrolle; keine überlappenden Crossfades, die Wörter beschädigen.
- [ ] Metadaten/Checkpoints müssen angewandte Bearbeitung versionieren; Original-WAV bei verlustbehafteter Nachbearbeitung erhalten oder rekonstruierbar lassen.

**Akzeptanz:** Automatische Grenz-/Dauer-/Clippingtests plus menschlicher A/B-Hörvergleich für ein zusammenhängendes Kapitel, ohne doppelte oder verschluckte Wörter.

### C02 — Hörbuch-Mastering und kompletter M4B-Export

**Fundstellen:** tts_audiobook_tool/sound/audio_writers.py, web_studio/production.py (export_job), EPUB-Metadatenextraktion, tests/audio/.

- [ ] Integrierte Lautheit, True Peak, Kanal-/Sampleratenkonsistenz und Endstille messbar prüfen; keine unkontrollierte Kompression oder Lautstärkesprünge.
- [ ] Titel, Autor, Erzähler, Sprache, sinnvoller Dateiname, vollständige Kapitelmarker und optional rechtmäßig nutzbares Cover in die Exportdaten übernehmen.
- [ ] Kapitel-IDs, Kapitelgrenzen und Pausenzeiten aus dem NarrationPlan übernehmen; genau eine AAC/M4B-Ausgabe per atomarem Dateicommit.
- [ ] Exportfortschritt getrennt von TTS-Fortschritt anzeigen. Vollständigen Decode, Kapitelreihenfolge, Anfang/Ende und gängige Player-Kompatibilität prüfen.
- [ ] Zusätzliche Kapitel-MP3 nur als eigenständige spätere Option, wenn sinnvoll und getestet; nicht anstelle des M4B.

**Akzeptanz:** Fehlerfrei decodierbares M4B mit korrekten Kapitelmarken und Metadaten; reproduzierbare Lautheitsmessung; keine verlorenen Segmente.

### D01 — Modellfähigkeiten sauber kapseln statt Parallelpipelines bauen

**Fundstellen:** tts_audiobook_tool/tts_models/qwen3_model.py, moss_model.py, web_studio/thorsten_worker.py, model_worker.py, docs-dev/tts-model-architecture.md.

- [ ] Minimale Capability-/Adapter-Schnittstelle für feste Sprecherprofile entwerfen: Modell-/Stimmenidentität, Lizenz, Sprache, Eingabe-/Ausgabeformat, deterministische Optionen, echte Style-/Context-/Pause-Unterstützung, Preflight, Inferenz- und Fehlerprotokoll.
- [ ] Aktuellen Thorsten-SFT-Worker als ersten Adapter einbinden, bestehende TUI-Modellimplementierungen für Qwen3/MOSS **wiederverwenden**, keine unnötigen Doppelmodelle.
- [ ] Modellwechsel setzt nur die betroffenen Cache-/Hörfreigaben zurück, verhindert gemischte Stimmen und erhält reproduzierbar abgeschlossene Bücher.
- [ ] Keine freie Referenz, kein Upload, kein Klonen; VoiceDesign im Webstudio bleibt außen vor. Ein Promptfeld erscheint nur bei explizit belegter Capability und Test, dass Inferenz die Anweisung akzeptiert und hörbar umsetzt.
- [ ] GPU-/RAM-Budgets, Modell-Ladezeiten und Konflikte bei isolierten Venvs dokumentieren.

**Akzeptanz:** Ein reproduzierbarer Text durch mindestens Thorsten plus einen optionalen testfähigen Adapter, identisches Eingabe-/Exportverhalten, sicherer Wechsel und korrekte UI-Fähigkeitsanzeige.

### D02 — Neutraler Hör- und Leistungsbenchmark

**Fundstellen:** runpod/voice_testset.json, runpod/VOICE_EVALUATION.md, runpod/ACCEPTANCE.md.

- [ ] 12 bestehende deutsche Testfälle unverändert für alle Kandidaten verwenden. Zusätzlich mindestens 20 Minuten zusammenhängenden, rechtmäßig nutzbaren Fachtext und ein vollständiges selbst verfasstes Test-EPUB.
- [ ] Thorsten-CosyVoice3, ein zulässiges festes Qwen3-CustomVoice-Profil und MOSS nur bei rechtlich abgesicherter fertiger Stimme testen. Verfügbarkeit/Revision im Testprotokoll dokumentieren; nicht unterstützte Kandidaten sauber ausweisen.
- [ ] Blindes A/B/C, vergleichbare Lautheit, mindestens zwei unabhängige menschliche Bewertungen. Natürlichkeit/Prosodie 30 %, Deutsch 25 %, Fachsprache 20 %, Konsistenz 15 %, Artefakte 10 %.
- [ ] Texttreue zusätzlich über ausgerichtete ASR-Verdachtsfälle und manuelle Kontrolle; schwerwiegende Auslassung, Wiederholung, falscher Zahlenwert oder ungeklärte Rechte sind Ausschlussgründe.
- [ ] GPU, Modell-/Torch-/CUDA-Revision, echter Peak-VRAM, Kalte-/Warmladezeit, reine Inferenzzeit, Audio-Sekunden, gesamte Job-Walltime, Retry/QA/Exportzeit und aktueller GPU-Tarif pro **erzeugter Hörstunde** erfassen.
- [ ] Keine Testwerte ohne echte WAV-Datei und nachvollziehbare Quelle. Keine personenbezogenen/private Audioartefakte öffentlich hochladen.

**Akzeptanz:** Reproduzierbare anonyme Hörprotokolle, gemessener RTF und Kosten, transparente Empfehlung. Ohne GPU und menschliche Bewertungen Status **BLOCKIERT**, nicht bestanden.

### D03 — Begründete Modellauswahl und echte Stilsteuerung

- [ ] Bestes freigegebenes fertiges Sprecherprofil anhand D02 als Standard wählen; Thorsten solange beibehalten, wie keine andere Stimme nachweisbar besser und rechtlich nutzbar ist.
- [ ] Intern Modell-/Profilwahl ermöglichen, ohne Endnutzer mit Low-Level-Parametern zu überfrachten.
- [ ] Falls ein geprüftes Modell echte Instruct-Fähigkeit bietet: begrenzte Stilvorgaben (z. B. ruhig/sachlich) für Preview und Produktion exakt gleich testen. Für inkompatible Adapter Feld vollständig ausblenden.
- [ ] Unbelegte Superlative und Qualitätsversprechen aus UI/README entfernen.

**Akzeptanz:** Standardsprecher und jede sichtbare Option beruhen auf belegtem Hörvergleich und passender Modellfunktion.

### E01 — Frontend-Qualität ohne Funktionsänderung

**Fundstellen:** web/src/studio.tsx, web/src/style.css, tests/web/browser_smoke.py.

- [ ] Großes React-Modul nach Bibliothek, EPUB/Kapitel, Sprecher, Glossar, Produktion/QA/Export und gemeinsame API-Hooks aufteilen; vorhandene UI/Flows erhalten.
- [ ] Fehlerzustände, lange Buchtitel, Responsivität, Tastaturbedienung, Fokus, Kontrast und Screenreader-Status prüfen.
- [ ] Keine ungeprüfte „Fortschritt in Prozent“-Anzeige für intern nicht beobachtbare Modellphasen. ETA nur bei tatsächlich berechenbaren Daten.

**Akzeptanz:** Bestehende Browser-Smoke-Tests grün; neue Ansichten und Fehlerszenarien gezielt geprüft, keine optische Rückentwicklung.

### E02 — Belastungs-, Sicherheits- und Recovery-Regressionen

- [ ] EPUB-ZIP-Bomben, Traversal, defekte Kapitel, hoher Textumfang, fehlendes Volume, volles Dateisystem, kaputte/teilweise WAV, ASR-Ausfall und CUDA-OOM simulieren.
- [ ] Pause/Cancel/Retry/Export/Delete unter echter und simulierter Parallelität testen. Keine verlorenen oder falsch freigegebenen Jobs.
- [ ] Private Token-/Buchtext-Leaks in API-Fehlern, Download-Routen, Logs und Git-Artefakten ausschließen.
- [ ] Modell- und Promptwechsel gegen Cache-Fingerprints und bestehende Hörfreigaben testen; alte Projekte und Neustart korrekt behandeln.
- [ ] M4B- und Browser-End-to-End-Tests auf finale integrierte Pipeline ausrichten.

**Akzeptanz:** Gezielt dokumentierte Fehlersimulationen ohne unbemerkte Datenverluste; neue Regressionstests auf dem jeweils aktuellen PR-Head.

### E03 — Reale RunPod-/Hörbuch-Abnahme und Release-Gate

**Vorbereitung:** runpod/ACCEPTANCE.md, runpod/VOICE_EVALUATION.md, runpod/acceptance.py. Nur mit tatsächlich autorisierter privater RunPod-Instanz und geeigneter GPU.

- [ ] Echte CUDA-Tensornutzung, Thorsten-Modellladen, Hörprobe und dokumentierte menschliche Sprecherfreigabe.
- [ ] Gültiges selbst verfasstes deutsches EPUB über authentifizierte Browser-/API-Pipeline ab Vorwort vertonen, vollständige Segmente prüfen, M4B erstellen, AAC und Kapitelmarker decodieren.
- [ ] Mindestens 20 Minuten Audio tatsächlich anhören, kritische Stellen und alle ASR-Flags prüfen; Zahlen, Namen, Fachsprache, Übergänge und Auslassungen dokumentieren.
- [ ] Laufenden Job nach Checkpoint **wirklich** durch Dienst-/Pod-Neustart unterbrechen und fertigstellen; keine doppelten/fehlenden Kapitel oder stillen Statusfehler.
- [ ] Reine Inferenz-RTF, komplette Erzeugungsdauer, GPU-Peak, Kosten pro Audiostunde und Exportzeit aus echten Messungen angeben. Fehlende Messungen offen lassen.
- [ ] GitHub-CI der finalen Head-SHA und vertrauliche Prüfprotokolle prüfen. Buchtext/Tokens/Referenzen bleiben privat.
- [ ] Release-Entscheidung als **bestanden / nicht bestanden / blockiert** mit Belegen und offenen Risiken in runpod/RELEASE_STATUS_2026-11-07.md oder einem aktuellen Nachfolgedokument festhalten; historisches Protokoll nicht stillschweigend als längst bestandenen Test umdeuten.

**Akzeptanz:** Reales vollständiges Hörbuch + menschliche Hörkontrolle + Neustart/Resume + technische Exportprüfung + dokumentierte Messergebnisse. Ohne diese Belege keine Produktionsfreigabe.

## 5. Empfohlene Teststrategie und Befehle

Befehle sind **Beispiele**, erst nach Prüfung des jeweiligen installierten Python-/Node-Umfelds ausführen; keine nicht vorhandenen Venvs als Erfolgsnachweis ausgeben.

~~~bash
# Für Webstudio-Änderungen: gezielte Tests zuerst
python -m pytest tests/web -q --confcutdir=tests/web

# EPUB-/Normalisierungsänderungen: relevante Eigentümer-Tests
python -m pytest tests/text/test_epub_extractor.py tests/text/test_prompt_normalizer.py -q

# Audiowriter/Export: passende Tests, inkl. No-FFmpeg-Regeln aus test-routing.md
python -m pytest tests/audio/test_audio_writers.py tests/audio/test_m4b_chapter_util.py -q

# Browser-UI auf dem gebauten Production Bundle
cd web && npm ci && npm run build && cd ..
python tests/web/browser_smoke.py

# RunPod-Checks nur auf autorisiertem echten Pod (siehe runpod/ACCEPTANCE.md)
python3 runpod/gpu_preflight.py --env both
~~~

Die dokumentierten CI-Workflows sollen Web/API, isolierte Web-Runtime, Frontend-/Chromium- und RunPod-Static-Verträge prüfen. **Keine** CI ohne GPU oder menschliches Hören als audiophile Qualitätsfreigabe darstellen.

## 6. Fortschrittsprotokoll (bei jeder abgeschlossenen PR aktualisieren)

| Paket | Status | PR / Commit | Gezielt ausgeführte Tests und Ergebnis | Offene Risiken / Freigabe |
|---|---|---|---|---|
| A01 | OFFEN | — | — | — |
| A02 | FERTIG (PR offen, nicht in master) | [PR #16](https://github.com/thomasasen/tts-audiobook-tool/pull/16), [Code-Commit 3a0a731](https://github.com/thomasasen/tts-audiobook-tool/commit/3a0a7310ea2214fbc063cf0f21dcc1e866cc0286) | [Studio CI #42371605004](https://github.com/thomasasen/tts-audiobook-tool/actions/runs/42371605004): Backend inkl. echtem AAC/M4B-Decode, Browser, Frontend, isolierte Runtime grün auf Code-Head; plan-only Head separat nachprüfen | Keine echte GPU/Hörabnahme; vor Merge A01-PR #15/plan.md koordinieren |
| A03 | OFFEN | — | — | — |
| A04 | OFFEN | — | — | — |
| B01 | OFFEN | — | — | — |
| B02 | OFFEN | — | — | — |
| B03 | OFFEN | — | — | — |
| B04 | OFFEN | — | — | — |
| C01 | OFFEN | — | — | — |
| C02 | OFFEN | — | — | — |
| D01 | OFFEN | — | — | — |
| D02 | OFFEN / GPU-ABNAHME VORAUSGESETZT | — | — | — |
| D03 | OFFEN | — | — | — |
| E01 | OFFEN | — | — | — |
| E02 | OFFEN | — | — | — |
| E03 | OFFEN / GPU- UND HÖRABNAHME VORAUSGESETZT | — | — | — |

Statuswerte: OFFEN, IN ARBEIT, BLOCKIERT (Grund angeben), FERTIG (Beleg), ENTFÄLLT (fachliche Begründung). Abhaken nur bei FERTIG; einmalige Statushinweise ändern die Checkbox nicht.

### A02 – technische Umsetzung im offenen PR #16 (07.11.2026)

**Basis:** `master` bei `6e59661d52728b09c7f4d5f3b76e1d5686364782`. `production._manifest()` speichert bereits pro Phrase-Gruppe `chapter` als stabilen book.v3-Abschnittsindex. Beim M4B-Export wurde zuvor ausschließlich der Anzeigetitel als Kapitelidentität verwendet; gleiche Nachbartitel verschmolzen und leere Titel unterdrückten Marker.

**Änderungen:** `tts_audiobook_tool/web_studio/production.py` verwendet `_export_chapter_key` zur Zuordnung über `chapter` bzw. das alte `chapter_index`, nur für ältere Pläne ohne beide Felder bleibt ein Titel-Fallback. Auch bei leeren Titeln werden Marker geschrieben. `tests/web/test_studio_export.py` ergänzt parametrisierten **echten** WAV→AAC/M4B-Encode/Decode und Kapitelmarkentests für doppelte Titel, leere Titel, übersprungene Kapitel und Start am Vorwort; Dauerprüfung gegen ausgelassene oder doppelte WAVs.

**Evidenz:** GitHub Actions `German Audiobook Studio`, Run `42371605004` auf Code-Commit `3a0a7310ea2214fbc063cf0f21dcc1e866cc0286`: Backend, Browser, Frontend und isolierte Web-Runtime bestanden. Keine echte TTS-GPU-Inferenz oder Hörabnahme; diese bleiben Release-Blocker. Die abschließende `plan.md`-Aktualisierung benötigt einen eigenen finalen CI-Nachweis. Wegen unabhängig gestarteter PRs auf `master` enthält PR #16 **nicht** die A01-Fortschrittsänderung aus PR #15; beim späteren genehmigten Merge der beiden PRs `plan.md` abgleichen, nicht überschreiben. Nächstes freies Paket: **A03**.

## 7. Übergabeformat für jeden neuen GPT-Chat

1. Ausgangsbranch und aktuelle SHA.
2. Gewähltes Paket inklusive Voraussetzung und überprüftem Ist-Befund.
3. Implementierungsentscheidung, bewusst nicht gewählte Alternativen und Migrationsrisiken.
4. Geänderte Dateien, Tests/CI samt konkreter Head-SHA, ggf. Screenshot-/GPU-/Hörbefunde.
5. PR-Link, offener Rest, aktualisierte Checkbox + Protokollzeile.
6. Nächstes **freies** Paket oder ausdrücklich erforderliche manuelle Abnahme.

**Startauftrag für neue Chats:** Dieses plan.md und AGENTS.md lesen, aktuellen master / PRs verifizieren und die erste noch offene, nicht blockierte Aufgabe in der Reihenfolge aus Abschnitt 3 tatsächlich implementieren. Keine bloße Neuplanung; keine erfundenen Tests; keine ungenehmigten Merges.