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
| [x] | A01 | P0 | keine | S | ASR-Installation und ehrliche Betriebsanzeige |
| [x] | A02 | P0 | keine | S | Kapitel-IDs statt Titel für M4B-Marker |
| [x] | A03 | P1 | keine | M | Reproduzierbare Releases, Abhängigkeiten und Modelle |
| [x] | A04 | P2 | keine | M | Speicherfreundliche, authentifizierte Downloads |
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

- [x] Entwicklungsmodus (bewusst aktueller master) von **Release-Modus mit festem Git-Commit** trennen; Checkout, Rollback und Neustart deterministisch.
- [x] Das aktuell verwendete CosyVoice-Framework, Matcha-TTS, Thorsten-Finetuning und CosyVoice-Basis-Modell mit vollständigen unveränderlichen Revisionen pinnen. Der Downloader verifiziert Dateien gegen Hugging-Face-LFS-SHA-256 bzw. Git-Blob-SHA-1 der jeweiligen Revision; das lokale SHA-256-Manifest sichert danach den persistenten Cache.
- [x] Python-/Node-Abhängigkeiten für getrennte Venvs reproduzierbar mit Hashes sperren; Installationspfade, Versionswechsel und Cache-Reparatur idempotent und fail-closed prüfen.
- [x] Unkontrollierte Updates beim Pod-Neustart verhindern; explizite Upgrade-/Rollback-Befehle, dokumentierte Mindestanforderungen und bewusst optionale Legacy-Modelle bereitstellen.
- [x] Offline-Regressionen für gültigen Cache, beschädigte Gewichte, falsche Revisionen, Versionsabweichungen, fehlenden Mount, CUDA-Fehler sowie Release-Backup und Restore ergänzen.
- [ ] Reale RunPod-Installation, echter Pod-Neustart mit Resume, Live-Upgrade/Rollback sowie OOM-/Volume-full-Fehlersimulation bleiben Teil der manuellen Live-Abnahme in E03.

**Code-Level-Akzeptanz:** Auf dem finalen PR-Head 8a1504e9d646de3df1b2263538a2841fea457558 waren RunPod Static (79 Tests), A03 Release Lock Generation und German Audiobook Studio erfolgreich. Die Code-Implementierung ist abgeschlossen.

**Live-Release-Gate:** Echte GPU-Inferenz, Pod-Neustart/Resume, reale Upgrade-/Rollback-Ausführung und menschliche Hörabnahme sind nicht durch CI belegt und bleiben vor Produktivbetrieb verpflichtend.

### A04 — M4B-Download ohne vollständiges Speicherpuffern

**Fundstellen:** web/src/studio.tsx, tts_audiobook_tool/web_studio/app.py.

- [ ] Bestehende Antwort-/Dateistream- und Range-Fähigkeiten prüfen. Authentifizierung und Downloadberechtigung behalten.
- [ ] Auf unterstützten Browsern die Datei stückweise auf ein vom Nutzer gewähltes Ziel streamen (z. B. File System Access API); für nicht unterstützte Browser einen klar begrenzten, beschriebenen Fallback wählen.
- [ ] Keine dauerhaft gültigen Token in URL oder Querystring, keine ungeschützten statischen M4B-Verzeichnisse.
- [ ] Abbruch, große Datei, fehlende Berechtigung, Browser-Fallback und unvollständigen Transfer testen.

**Akzeptanz:** Mehrstündige M4B-Dateien benötigen nicht zwingend einen entsprechend großen JS-Blob im RAM; ein fehlgeschlagener Download wird als solcher angezeigt.

### B01 — EPUB-Verlustfreiheit und nachvollziehbare Auslassungen

**Fundstellen:** tts_audiobook_tool/text_ops/epub_extractor.py, epub_content_audit.py, epub_section_skip_detector.py, tts_audiobook_tool/web_studio/app.py, web/src/studio.tsx, tests/text/test_epub_*.py, tests/web/test_real_epub_e2e.py.

- [x] EPUB-Spine, Navigationsanker, Unterabschnitte, Inline-Formatierung, Tabellen, Listen, Fußnoten, Bildunterschriften, MathML und Scene-Break-Markup anhand selbst verfasster Test-EPUBs auditieren.
- [x] Automatisch ausgelassene Inhalts-/Metadatenabschnitte mit Kategorie, Grund und Vorschau sichtbar machen; freigegebene Publication-Metadata-/TOC-Heuristiken im Entwurfsstatus korrigierbar gestalten. Navigation-only-, fehlende und nicht dekodierbare Ressourcen bleiben geschützt.
- [x] Gemischte XHTML-Dokumente mit Navigation und Fließtext auch dann erhalten, wenn die Prosa in Nicht-Paragraph-Containern liegt; linkdichte Navigationslisten bleiben ausgeschlossen.
- [x] Optionale, ausschließlich manuell formulierte und explizit übernommene Sprecherfassungen für Tabellen, MathML, Figures und eigenständige Bilder mit Vorschau; keine automatische Inhaltserfindung, Original-EPUB unverändert.
- [x] Quelltext-zu-Vorlesetext-Mapping mit Quell-/Output-Zeichenoffsets und Nicht-Leerzeichen-Abdeckung; manuelle Overrides werden beim Project-V5-Import erneut angewendet. Bestehende Projekte bleiben lesbar.

**Technische Akzeptanz:** Synthetische Regressionen und E2E prüfen Navigation, Quellzuordnung, Heuristik-Wiederaufnahme, Sprecherfassungen und tatsächliche Project-V5-Phrase-Groups. CI-Run [42407963183](https://github.com/thomasasen/tts-audiobook-tool/actions/runs/42407963183) auf PR-Head `d1a903db5db81a67d797c2199ffff0d0dca2a028` vollständig erfolgreich.

**Noch ausstehende redaktionelle Abnahme:** Mindestens ein komplexes, rechtmäßig nutzbares privates EPUB muss im Studio gegen das Original geprüft werden. Mapping-Abdeckung von 100 Prozent belegt nur die Zuordnung des übernommenen Textes, nicht die semantische Vollständigkeit von Tabellen, Formeln, Abbildungen und Fußnoten. B01 bleibt bis zu dieser manuellen Prüfung offen.

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
| A01 | IN MASTER · technische Abnahme bestanden | [PR #15](https://github.com/thomasasen/tts-audiobook-tool/pull/15) | Sechs CI-Checks einschließlich echter deutscher CPU-ASR auf finalem Commit erfolgreich | Keine GPU-/Hörabnahme; Produktrelease offen |
| A02 | IN MASTER · technische Abnahme bestanden | [Integrations-PR #19](https://github.com/thomasasen/tts-audiobook-tool/pull/19) (ersetzt #16) | Fünf CI-Checks auf kombiniertem A01/A02-Stand grün, einschließlich echtem M4B-Decode | Keine menschliche Hörabnahme |
| A03 | CODE-IMPLEMENTIERUNG IN MASTER · LIVE-ABNAHME OFFEN | [PR #24](https://github.com/thomasasen/tts-audiobook-tool/pull/24), Merge e9085c7 | 79 RunPod-Static-Tests, A03 Lock Generation und German Audiobook Studio auf finalem Head 8a1504e grün | Echte RunPod-GPU, Pod-Neustart/Resume, Live-Upgrade/Rollback und Hörabnahme bleiben in E03 offen |
| A04 | IN MASTER · technische Abnahme bestanden | [Integrations-PR #20](https://github.com/thomasasen/tts-audiobook-tool/pull/20) (ersetzt #18) | Fünf CI-Checks inklusive Browser-Streaming, 96-MB-Fallback und Range-206 grün | Große echte RunPod-M4B noch nicht heruntergeladen |
| B01 | TECHNISCH UMGESETZT · PRIVATE EPUB-ABNAHME AUSSTEHEND | [PR #22](https://github.com/thomasasen/tts-audiobook-tool/pull/22), [PR #26](https://github.com/thomasasen/tts-audiobook-tool/pull/26), [PR #27](https://github.com/thomasasen/tts-audiobook-tool/pull/27), [PR #28](https://github.com/thomasasen/tts-audiobook-tool/pull/28) | OPF-/Spine-Audit, Quell-/Vorlesetext-Mapping, Abdeckungsquote, korrigierbare Heuristik-Auslassungen, manuelle Sprecherfassungen, Erkennung ausgelagerter Fußnoten außerhalb des Spine; Backend, Chromium-E2E, Frontend, CPU-ASR und isolierte Runtime grün | Ein komplexes privates EPUB muss noch manuell im Studio geprüft und redaktionell abgenommen werden; Mapping-Prozent ersetzt keine semantische Vollständigkeitsprüfung |
| B02 | TEILWEISE IN MASTER · NICHT ABGESCHLOSSEN | [PR #23](https://github.com/thomasasen/tts-audiobook-tool/pull/23) | Fünf CI-Checks grün; Golden Cases und rückwärtskompatible Versionierung des Cache-Fingerprints | Umfangreiche Spezialfalltests, Vorschauparität B04 und Hörabnahme fehlen |
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

### A01 – technische Umsetzung im offenen PR #15 (07.11.2026)

**Geprüfter Ausgangsstand:** `master` bei `6e59661d52728b09c7f4d5f3b76e1d5686364782`. PR #6 und #1 sind unabhängig offen; PR #14 ist gemergt. Der Web-Venv deklarierte ASR nicht, während `start-web.sh` `small` anforderte.

**Änderungen auf `fix/a01-asr-quality-readiness`:**
`tts_audiobook_tool/web_studio/asr_quality.py` (Laufzeitstatus, CPU-Inferenz, Metriken), `production.py` (Sicherheits- und Prüfbericht-Integration), `app.py` (authentifizierte Capabilities), `web/requirements-runtime.txt`, `runpod/install-web.sh`, `runpod/start-web.sh`, `web/src/studio.tsx`, `tests/web/test_asr_quality.py`, `tests/web/test_studio_production.py`, `tests/web/browser_smoke.py`, `runpod/asr_cpu_smoke.py`, `.github/workflows/german-audiobook-studio.yml` und `docs-dev/asr-quality-checks.md`.

**Vertrag/Risiken:** Keine Änderung am Project-V5-Schema oder Originaltext. ASR bleibt nur Verdachtsprüfung, bei Fehler `not_checked`. Bereits gespeicherte Beta-Segmentprüfungen sind abwärtskompatibel lesbar; frühere private Transkripte werden nicht migriert. `small` wird erst beim echten Einsatz geladen. **Nachweis:** Auf Code-Head `1cd6f6e9ddc23b5b408b36549407c6b1e53d168c` bestanden: Python-Webtests, Browser-Chromium, Frontend-Build, echte isolierte Web-Venv-Installation, RunPod-Static und realer deutscher CPU-`tiny`-Whisper-Smoke mit Negativfall (Actions-IDs in der Tabelle). Der abschließende plan-only Head muss in CI ebenfalls grün sein; vor einem Merge bitte exakt den finalen PR-Head prüfen. CPU-`small` auf RunPod, GPU und menschliches Anhören bleiben separate **Release-Gates**, keine A01-Behauptung.

### A02 – technische Umsetzung im offenen Integrations-PR von #16 (07.11.2026)

**Basis:** `master` bei `6e59661d52728b09c7f4d5f3b76e1d5686364782`. `production._manifest()` speichert bereits pro Phrase-Gruppe `chapter` als stabilen book.v3-Abschnittsindex. Beim M4B-Export wurde zuvor ausschließlich der Anzeigetitel als Kapitelidentität verwendet; gleiche Nachbartitel verschmolzen und leere Titel unterdrückten Marker.

**Änderungen:** `tts_audiobook_tool/web_studio/production.py` verwendet `_export_chapter_key` zur Zuordnung über `chapter` bzw. das alte `chapter_index`, nur für ältere Pläne ohne beide Felder bleibt ein Titel-Fallback. Auch bei leeren Titeln werden Marker geschrieben. `tests/web/test_studio_export.py` ergänzt parametrisierten **echten** WAV→AAC/M4B-Encode/Decode und Kapitelmarkentests für doppelte Titel, leere Titel, übersprungene Kapitel und Start am Vorwort; Dauerprüfung gegen ausgelassene oder doppelte WAVs.

**Evidenz:** GitHub Actions `German Audiobook Studio`, Run `42371605004` auf Code-Commit `3a0a7310ea2214fbc063cf0f21dcc1e866cc0286`: Backend, Browser, Frontend und isolierte Web-Runtime bestanden. Keine echte TTS-GPU-Inferenz oder Hörabnahme; diese bleiben Release-Blocker. Die abschließende `plan.md`-Aktualisierung benötigt einen eigenen finalen CI-Nachweis. Wegen unabhängig gestarteter PRs auf `master` enthält PR #16 **nicht** die A01-Fortschrittsänderung aus PR #15; beim späteren genehmigten Merge der beiden PRs `plan.md` abgleichen, nicht überschreiben. Nächstes freies Paket: **A03**.


### A04 – technische Umsetzung im offenen PR #18 (07.11.2026)

**Ausgangsbefund:** `web/src/studio.tsx` verwendete `Response.blob()` zur vollständigen M4B-Übernahme in den Browser-RAM. Auch das Vollhörbuch-Player-Feature lud das gesamte M4B in einen Blob. Der authentifizierte `FileResponse` konnte prinzipiell bereits gestreamt werden.

**Änderungen:** Neues `web/src/download.ts` schreibt auf kompatiblen Desktop-Browsern (File System Access API) die authentifizierte Antwort direkt stückweise in die vom Benutzer gewählte Datei. Abbruch per AbortController und `FileSystemWritableFileStream.abort()`, fehlender Body, HTTP-/Content-Type-Fehler, Transferlängen und fehlende Berechtigung sind abgegrenzt. Andere Browser erhalten nur einen klar dokumentierten Fallback von maximal 96 MiB im RAM und eine Erklärung für größere Dateien; kein dauerhaftes Zugangs-Token in URL, Querystring oder lokalem Speicher. `web/src/studio.tsx` zeigt Downloadfortschritt und Abbruch, entfernt den zweiten unbegrenzten Vollhörbuch-Blob-Pfad (Einzelsegmente bleiben abspielbar). `tts_audiobook_tool/web_studio/app.py` setzt `Cache-Control: private, no-store` für M4B-Antworten. `tests/web/browser_smoke.py` führt einen echten Browser-Stream-Sink und übergroßen Fallback durch; `tests/web/test_studio_export.py` bestätigt authentifizierte Range-206-Downloads, Datenschutzheader und 401 ohne Token.

**Evidenz:** Code-Commit `53bb76e2e42042a906913b2c3f686666194527ec`; GitHub Actions `42372700653`: Backend, Frontend, Browser, isolierte Web-Runtime **erfolgreich**. Der finale Fortschrittscommit muss ebenfalls CI-grün bleiben. Keine echte große private RunPod-Datei, keine Browser-Untersuchung auf Firefox/Safari-Hardware. A04 hat damit die offline prüfbaren Kriterien erfüllt. A01 (PR #15), A02 (PR #16), A03 Teil 1 (Draft-PR #17) sind auf separaten noch ungemergten Master-Abzweigungen; `plan.md` vor einem genehmigten Merge abgleichen.


### A03 – Historischer Zwischenstand vor PR #24 (Draft-PR #17)

**Damals offener Befund:** runpod/autostart.sh besaß bereits den optionalen Release-Parameter RUNPOD_TTS_COMMIT (vollständige 40-Hex-SHA; nur master erlaubt; Zugehörigkeit zur Master-Historie und exakte HEAD-Gleichheit kontrolliert). Ohne Commit blieb der bewusste Entwicklungsmodus. Die vollständigen Quell-/Modellrevisionen und reproduzierbaren Abhängigkeits-Locks fehlten zu diesem Zwischenstand.

**Teil 1 umgesetzt:** runpod/autostart.sh und runpod/README.md trennten bereits Entwicklungs- und Release-Modus. tests/runpod/test_runpod_autostart.py prüfte ungültige Werte/Branches und Offline-Vertragsinvarianten.

**Abschluss:** Die damals offenen Quell-/Modell-Pins, Hash-Locks und die Code-seitige Upgrade-/Rollback-Transaktion wurden in PR #24 umgesetzt und in master integriert. Die echte RunPod-Installation und Pod-Neustart-Abnahme bleiben ausdrücklich offen (siehe E03).

### A03 – Abschluss der Code-Implementierung in PR #24 (07.11.2026)

**Merge:** PR #24, Merge-Commit e9085c718969f8c693fc32c74684149302726c75. Getesteter PR-Head: 8a1504e9d646de3df1b2263538a2841fea457558.

**Änderungen:**
- runpod/model-pins.env pinnt CosyVoice, Matcha-TTS, das CosyVoice-Basismodell und Thorsten auf vollständige Revisionen.
- download-hf-models.py lädt nur benötigte Assets aus den gepinnten Revisionen; verify-hf-models.py prüft Upstream-LFS-SHA-256 bzw. Git-Blob-SHA-1, bevor ein lokales Cache-Manifest geschrieben wird.
- Hash-Locks für uv-Bootstrap, Thorsten, Qwen, MOSS, Web-Runtime, CPU-Torch und npm; runpod/locks/SHA256SUMS kontrolliert die Lock-Artefakte.
- release_transaction.py sichert persistente Runtime-Assets atomar, prüft Release-Fingerprints und ermöglicht Restore mit Vor-Rollback-Snapshot. upgrade-release.sh und rollback-release.sh stellen explizite Operator-Befehle bereit.
- Offline-Regressionen prüfen Modellrevisionen, beschädigte Caches, gültigen Prefetch, Runtime-Versionen, Mount-/CUDA-Fehler und Backup/Restore.

**CI-Evidenz auf dem endgültigen PR-Head:**
- RunPod setup static tests #42397452055: erfolgreich, 79 Tests.
- A03 release lock generation #42397452050: erfolgreich; fehlende Lockfiles wurden erzeugt und eingecheckt.
- German Audiobook Studio #42397452058: erfolgreich, einschließlich Backend, Frontend, Chromium, isolierter Web-Runtime und deutschem CPU-ASR-Smoke.

**Nicht durch diese CI belegt:** echte CUDA-/Thorsten-Inferenz, ein tatsächlicher Pod-Neustart, ein Live-Upgrade mit automatischem Rollback, OOM-/Volume-full-Verhalten, lange EPUB→M4B-Produktion und menschliche Hörqualität. Diese Release-Gates bleiben in E03.

**Abgleich PR #6:** PR #6 ist 389 Commits hinter master und 20 Commits voraus; der Branch ist divergiert und nicht mergefähig. doctor.py und start.sh sind bereits identisch mit master. Die PR-Versionen von autostart.sh, setup.sh, env.sh, Workflow und Tests sind durch die aktuelle Master-Implementierung überholt. README.md und runpod/README.md unterscheiden sich; mögliche noch fehlende TUI-Hinweise bei Bedarf gezielt prüfen. PR #6 nicht als Ganzes mergen.

### B01 – Teilimplementierung in PR #22

**Befund:** `load_source_chapters` verwirft Dokumente mit EPUB-3-Nav-Metadaten standardmäßig vollständig, selbst wenn zusätzliche echte Prosa außerhalb von `<nav>` liegt. Der vorhandene BeautifulSoup-Extractor überspringt `<nav>`-Elemente bereits, wenn das XHTML als Quelle verarbeitet wird.

**Lösung (Teil 1):** Konservatives Prosasignal aus normalen XHTML-`p`-/`blockquote`-Absätzen außerhalb `<nav>`, unter Wiederverwendung des vorhandenen Textextraktors. Gemischte Ressourcen werden nicht mehr pauschal ausgeschlossen; reine Navigationslisten bleiben ausgenommen. Warnung mit Resource-Href weist auf den Sonderfall hin. Synthetische Regressionen in `tests/text/test_epub_extractor.py`.

**Noch offen nach PR #22:** Vollständiger B01-Content-Audit, Quell-Sprech-Mapping, klickbare Ausschlusskorrektur, komplexe Fußnoten-/Formel-/XHTML-Regressionen sowie ein komplexes privates EPUB und dessen menschliche Abnahme. **B01 nicht abgeschlossen**.

### B01 – Quelleninventur und sichtbarer Audit in PR #26 (07.11.2026)

**Merge:** PR #26, Merge-Commit c74acf770ad3387f7463d285a16f4b0b072e830a. Getesteter PR-Head: 08cb1e40a51e9db2e8f1f80dfd1022e6e7199e94.

- Neuer read-only Audit liest EPUB container.xml, OPF-Manifest und Spine ohne die kanonische Extraktion zu ersetzen.
- XHTML-Quellen werden mit Textvorschau sowie Tabellen, Listen, Abbildungen, Bildunterschriften, MathML und Überschriften inventarisiert.
- Nicht zugeordnete Spine-Ressourcen werden mit Status und nachvollziehbarem Grund in der geschützten Projektansicht sichtbar gemacht.
- Synthetische EPUB-Audit-Tests und der echte Studio-E2E-Test prüfen die API-Vertragsintegration. Original-EPUB und Project-V5-Persistenz bleiben unverändert.
- CI-Run 42405175578 auf dem exakten PR-Head erfolgreich: Backend, Browser/Chromium, Frontend-Build, CPU-ASR-Smoke und isolierte Runtime.

**Grenze:** Der aktuelle Audit weist Ressourcen zu, beweist aber noch keine zeichen-/segmentgenaue Quell-zu-Sprechtext-Abdeckung. Nicht zugeordnete Navigation kann auch in zusammengeführte Abschnitte eingeflossen sein. Es gibt noch keine UI-Funktion, mit der erkannte Auslassungen korrigiert oder eine alternative Sprecherfassung für Tabellen/Formeln/Abbildungen freigegeben werden kann. B01 bleibt offen.

### B01 – Mapping, korrigierbare Auslassungen und Sprecherfassungen in PR #27 (07.11.2026)

**Merge:** PR #27, Merge-Commit 2ecd49255d101d3480e6cf089fb9fb1de33cf930. Getesteter PR-Head: d1a903db5db81a67d797c2199ffff0d0dca2a028.

- Ergänzt Quell-/Output-Zeichenoffsets je Textspanne und eine Abdeckungsquote über alle Nicht-Leerzeichen im übernommenen Vorlesetext.
- Macht Skip-Kategorie, Grund, Vorschau und sichere Wiederaufnahme von Publication-Metadata-/TOC-Heuristiken sichtbar. Die manuelle Aufnahme ist nur im Status Entwurf erlaubt; nach der Kapitelauswahl ist sie gesperrt.
- Erhält gemischte XHTML-Navigationsressourcen mit Prosa auch in Nicht-Paragraph-Containern; reine Navigation und linkdichte TOC-Listen bleiben ausgenommen.
- Ergänzt ausdrücklich manuelle Sprecherfassungen für Tabellen, MathML, Figures und eigenständige Bilder. Diese ersetzen nur das jeweilige Markup, erhalten Ankerpositionen und werden bei der Project-V5-Übernahme erneut angewendet.
- Dokumentiert Semantik, Grenzen und manuelle Abnahme in `docs-dev/epub-content-audit.md`.
- Synthetischer E2E-Test: Copyright-Heuristik wieder aufnehmen/zurücksetzen, Quell-Mapping prüfen, Sprecherfassungen anwenden, gespeicherte Project-V5-Phrase-Groups prüfen und nach vorbereiteter Auswahl Änderungen blockieren.
- CI-Run 42407963183 auf exakt diesem PR-Head vollständig erfolgreich: Backend, Browser/Chromium, Frontend-Build, CPU-ASR-Smoke und isolierte Runtime.

**Grenze:** Mapping-Abdeckung beweist die Zuordnung des übernommenen Textes, nicht semantische Vollständigkeit komplexer Elemente. B01 bleibt bis zur manuellen Abnahme mindestens eines komplexen privaten EPUBs offen.

### B01 – ausgelagerte Fußnoten außerhalb des Spine in PR #28 (07.11.2026)

**Merge:** PR #28, Merge-Commit 86ee719579b67059aa373271189af7306f49e36c. Getesteter PR-Head: 91dc0163ac40a72d40db589f324740415eee7206.

- Fußnotenverweise mit `doc-noteref`/`noteref` werden auf ihre href-Ziele geprüft.
- XHTML-Fußnotenressourcen außerhalb des EPUB-Spine sowie fehlende Ziele erscheinen mit Quell-/Zielpfad, Status, Grund und Vorschau im Audit.
- Diese Ressourcen werden bewusst nicht automatisch in den Vorlesetext übernommen und nicht als über Spine-Overrides wiederaufnehmbar ausgegeben.
- Synthetischer Regressionstest deckt eine Fußnote innerhalb des Spine und eine ausgelagerte Fußnote außerhalb des Spine ab.
- CI-Run 42408335095 auf dem exakten PR-Head erfolgreich: Backend, Browser/Chromium, Frontend-Build, CPU-ASR-Smoke und isolierte Runtime.

### B02 – Versionierte Sprechtext-Normalisierung (Teilimplementierung)

**Code:** `tts_audiobook_tool/text_ops/german_speech_normalizer.py` übernimmt kontrollierte deutsche Umwandlungen von Dezimal-, Euro-, Prozent- und mehrwortigen Glossarbegriffen. Vorhandene generische Prompt-Normalisierung bleibt für allgemeine Interpunktion/Leerraum zuständig; ihre per-Ziffer-Erweiterung wird für deutsche Bücher deaktiviert, um Dezimalbruchwerte nicht versehentlich in zwei ganze Zahlen zu zerlegen. Zeiten, Daten, URLs, Mailadressen, lange Identifikatoren und nicht freigegebene Akronyme werden nicht willkürlich geraten.

**Abwärtskompatibilität:** `web_studio/production.py` legt `normalizer_version=de-narration-v2` nur für neue Jobs ab. Bestehende Jobs ohne Version verwenden weiter exakt `_normalize_de_legacy` samt alter SHA-256-Cache-Fingerprints. Neue Jobs verwenden einen versionierten Fingerprint. Bei unbekannter Version wird nicht still produziert.

**Evidenz:** `tests/text/test_german_speech_normalizer.py` mit Zahlen, Glossar, Schutzfällen, Alt-/Neu-Hash und Legacy-Pfad; GitHub Actions backend um diese Tests ergänzt. CPU-/Browser-/RunPod-CI des finalen Commits noch prüfen.

**Offen:** Erweiterte kontextabhängige Sprache (Eigennamen, komplexe Abkürzungen, schwierige Tabellen), Quelle→Prompt-Protokoll und Produktions-Hörprobe-Parität aus B04. Noch keine reale Modell-Hörkontrolle. Daher **B02 IN ARBEIT**, keine endgültige Audioqualitätsabnahme.

### Integrationsstand auf `master` vom 07.11.2026

**Verifizierter Repository-Stand:** master nach A01, A02, A03-Codeabschluss, A04, B01-Navigationskorrekturen (PR #22), B01-Quelleninventur (PR #26), B01-Mapping-/Review-Steuerung (PR #27, Merge 2ecd492) und ausgelagerte Fußnotenprüfung (PR #28, Merge 86ee719), außerdem B02 Teil 1. Der Standardbranch heißt master, nicht main.

**Wichtig:** B01 ist technisch umgesetzt; offen bleibt ausschließlich die redaktionelle Abnahme mit einem komplexen privaten EPUB. B02 ist weiterhin teilweise umgesetzt. A01, A02, A03 (Code-Implementierung) und A04 haben ihren offline prüfbaren technischen Umfang abgeschlossen. Bei A03 bleiben echte RunPod-Installation, Pod-Neustart/Resume und Live-Rollback Teil der Release-Abnahme E03. B03, B04, C01, C02, D01, D02, D03, E01, E02 und E03 bleiben offen; bei D02/E03 fehlen echte GPU- und menschliche Hörbewertungen. Keine finale Releasefreigabe und keine Behauptung einer echten End-to-End-RunPod-Hörbuchproduktion.

**Nächster Schritt:** Ein komplexes privates EPUB im Studio manuell abnehmen. Bei bestandener Prüfung B01 schließen und anschließend B02 um kontextabhängige Aussprache erweitern; danach B03 NarrationPlan vorbereiten.

## 7. Übergabeformat für jeden neuen GPT-Chat

1. Ausgangsbranch und aktuelle SHA.
2. Gewähltes Paket inklusive Voraussetzung und überprüftem Ist-Befund.
3. Implementierungsentscheidung, bewusst nicht gewählte Alternativen und Migrationsrisiken.
4. Geänderte Dateien, Tests/CI samt konkreter Head-SHA, ggf. Screenshot-/GPU-/Hörbefunde.
5. PR-Link, offener Rest, aktualisierte Checkbox + Protokollzeile.
6. Nächstes **freies** Paket oder ausdrücklich erforderliche manuelle Abnahme.

**Startauftrag für neue Chats:** Dieses plan.md und AGENTS.md lesen, aktuellen master / PRs verifizieren und die erste noch offene, nicht blockierte Aufgabe in der Reihenfolge aus Abschnitt 3 tatsächlich implementieren. Keine bloße Neuplanung; keine erfundenen Tests; keine ungenehmigten Merges.