# Weboberfläche: German-First Audiobook Studio (ohne Stimmstudio)

**Status:** verbindliches Umsetzungskonzept, **noch keine implementierte Weboberfläche**  
**Repository:** `thomasasen/tts-audiobook-tool`  
**Scope:** deutscher Fachbuch-Workflow; bestehende TUI und TTS-Infrastruktur erhalten  
**Priorisierte Abhängigkeiten:** #2 (RunPod), #3 (EPUB), #4 (Sprecher), #5 (deutsche QA)

## 1. Produktentscheidung

Die Webanwendung ist **kein Stimmstudio**. Endnutzer sollen keine Stimmen aufnehmen, hochladen, synthetisch gestalten, klonen oder mittels Sampling-Parametern konfigurieren. Stattdessen:

1. Fertiges deutsches Sprecherprofil auswählen.
2. Eine vorgerenderte bzw. bei Bedarf generierte **deutsche Hörprobe** anhören.
3. Deutschen EPUB-Text prüfen und Beginn beim **Vorwort** wählen.
4. Hörbuchgenerierung mit einem Button starten.
5. Kapitelweise Fortschritt, Fehler und Wiederholungsversuche kontrollieren.
6. Ergebnisse als M4B und optional Kapitel-MP3 herunterladen.

Für *The Qualified Sales Leader* ist der gewünschte Standard **ein männlicher, warm-souveräner, lebendiger Sprecher ohne amerikanischen Grundakzent**. Die UI startet immer in deutscher Sprache. Englische Fachbegriffe im Buch werden gezielt behandelt, ohne die gesamte Ausgabe auf Englisch zu stellen.

**Nicht enthalten:** Stimmstudio, freie Reference-Audio-Uploads, Voice-Design-Prompts, Stimmparameter-Editor, Mehrstimmen-Drama, automatische übersetzende Textumschreibung, englische Modell-Preset-Stimmen als deutscher Fallback. Auch kein TUI-in-Browser-Wrapper.

## 2. Fertige Sprecherprofile (kuratierter Katalog)

Der Sprecherkatalog besteht aus geprüften und versionierten Presets, nicht aus beliebigen Modellstimmen:

- `id`, `display_name`, `description_de`, `language_code=de`
- `speaker_identity` (falls bekannt) und Stilvariante; **unterschiedliche Stile derselben Person sind keine unterschiedlichen Stimmen**
- `reference_audio_source`, `reference_text` (exakte Abschrift!), SHA-256, Länge, Sample-Rate, Qualität
- `license`, `source_url`, `consent_basis`, `verified_at`, ggf. Nutzungsbeschränkungen
- `preferred_model`, erprobte modellabhängige Werte, deutscher Fachbegriff-Aussprachetest
- `preview_audio` mit geprüftem, eigenem/erlaubtem Beispielsatz, nicht mit unzulässig veröffentlichten Buchpassagen
- `status=ready|unavailable|under_review`; nur `ready` im normalen Dropdown

Referenzaudio wird bei Bedarf mit validierter Quelle/Hash auf das **private RunPod-Volume** geladen, nicht ins öffentliche Git aufgenommen. Keine unbekannten URLs ungeprüft herunterladen und keine willkürliche Voice-Cloning-Referenz aus Medien/Promi-Aufnahmen verwenden.

### Verifizierter erster Quellenkandidat

[Thorsten-Voice TV-44kHz-Full](https://huggingface.co/datasets/Thorsten-Voice/TV-44kHz-Full), CC0, native deutsche Männerstimme, inklusive transkribierter neutraler und emotionaler Aufnahmen. Das Dataset dokumentiert **einen** Sprecher, keine Sprecherbibliothek. Eine neutrale und eine emotionalere Referenz können als **Stilvarianten von Thorsten** gegeneinander geprüft werden, nicht als zwei unterschiedliche Sprecher. Originalsprecher weist selbst auf die Grenzen seiner professionellen Sprecherfahrung hin; daher keine Qualitätsgarantie.

Rechtlich zulässige **weitere männliche Stimmen** werden nur aufgenommen, falls eindeutig nutzbare Referenzclips und Zustimmung zum synthetischen Nachbilden vorliegen (z. B. ausdrücklich lizenzierte professionelle Sprecher). Keine erfundenen Stimmen oder ungeprüften „kostenlosen“ Soundclips listen.

### Interner Modellvergleich

Qwen3-TTS **Base** (nicht CustomVoice-Ryan) und MOSS-TTS v1.5 werden mit denselben deutschen Beispielsätzen und Referenzclips geprüft. Ein **einfaches Preset** in der Weboberfläche wird automatisch auf die passende getestete Engine abgebildet. Nutzer sollen keine GPU-, Torch-, Prompt-Sampling- oder Stimmklonungsoptionen auswählen müssen.

## 3. German-first Sprachpipeline

- **Deutsch (`de`) als durchgängiger Standard**: Import, Segmentierung, Generierung, STT, Fehlerprüfung und Oberfläche. Nicht lediglich Anzeigeübersetzung.
- Inferenzmodelle ausdrücklich auf **German** stellen, keine automatische unkontrollierte Sprachdetektion pro Kapitel, kein US-Speaker-Preset als Fallback.
- Ein deutsches Ausspracheprofil für Fachwörter und Akronyme: `MEDDPICC`, `CRO`, `QBR`, `Champion`, `Economic Buyer`, `Discovery`, `Forecast`, `POC` usw. **Buchkontext und Modell testen**, nicht pauschal alle Anglizismen ins Deutsche umschreiben.
- Bestehende `word_substitutions` nutzen; interne Glossar-Regeln versionieren, priorisieren und nachvollziehbar testen. Schriftbild des Originals bleibt unverändert; Ausspracheänderungen nur für TTS-Prompt.
- Deutsche Textnormalisierung: `ä/ö/ü/ß`, Ziffern und ausgeschriebene Zahlen, Dezimaltrennzeichen, Abkürzungen (`z. B.`, `u. a.`, `GmbH`), Komposita, Bindestriche, Eigennamen und mehrsprachige Einschübe.
- German STT-Validierung: echte Auslassungen/Wiederholungen von Whisper-Artefakten trennen, automatische Wiederholversuche und gezieltes manuelles Abhören.
- Gute **nonfiction-Prosodie**: saubere Sinnpausen, natürliche Satzmelodie, souveräne Sachlichkeit, lebendig ohne künstliche Emotionalisierung, langfristig stabile Sprecheridentität. Durch Hörtests nachweisen, nicht nur über Parameter behaupten.
- EPUB: Startposition vom Nutzer als Kapitel/Abschnitt bestätigen, **Vorwort als empfohlener Start**, niemals automatischer Beginn beim Inhaltsverzeichnis. Retained chapter order and content preview.

Die vorhandene Konsolen-App bleibt mehrsprachig und unberührt; das neue Web-Produkt ist **deutsch fokussiert**.

## 4. Oberfläche: fünf Schritte, bewusst reduziert

### Dashboard / Bibliothek
- Projekte als Buchkarten mit Titel, Kapitelanzahl, Status und zuletzt verwendetem Preset
- EPUB importieren oder vorhandenes Projekt öffnen; Weiterführung unterbrochener Jobs
- Deutsch als Standard, keine englische Sprachausgabe per versehentlicher Modelldefault-Wahl

### Text und Kapitel
- Lesbare Kapitelübersicht und Probeauszug vor Generierung
- Start beim Vorwort auswählen (Vorschlag anhand EPUB-Navigation, aber Bestätigung und Vorschau)
- Warnungen zu Umbrüchen, Initialbuchstaben, fehlenden Abschnitten; keine lautlose automatisierte Textänderung

### Sprecher auswählen
- **Nur fertige Karten** (Name/Stil, kurze Beschreibung, Lizenzhinweis, deutsche Hörprobe, Auswahl)
- Keine Upload- oder Aufnahmefelder, keine Schieberegler für Tonhöhe/Emotion/Temperatur
- Standardmäßig ein geprüftes männliches Fachbuch-Preset vorselektiert; falls nicht verfügbar, verständlicher Fehler statt US-Ersatzstimme

### Hörbuch erstellen
- Große Aktion „Hörbuch erzeugen“, vor Beginn Zusammenfassung (Buch, Startkapitel, Sprecher, Ausgabeformat)
- Live-Fortschritt nach Kapitel/Satzsegment, Prozent und vorsichtiger ETA; Retry- und Fehlerstatistik
- Cancel/Pause/Resume als kontrollierte Queue-/Worker-Aktionen, sicherer Persistenzstatus
- Nicht gleichzeitig zwei GPU-Inferenzjobs mit vollem Modellwechsel; Warteschlange oder klarer Wechselzustand

### Prüfen und exportieren
- Kapitelprobe und Problemsegment direkt anhören
- Export als M4B mit Kapiteln, optional MP3 je Kapitel
- Bestehenden `browser_player/` für Text-Audio-Synchronisation wiederverwenden, nicht nachbauen

Design: reaktionsschnelles, klares, zurückhaltendes Interface (light/dark, zugänglich, volle Tastaturbedienbarkeit, deutschsprachige Fehlermeldungen). Fokus auf Lesbarkeit, Statusklarheit und möglichst wenige technische Einstellungen.

## 5. Architektur und Sicherheitsgrenzen

**Bestehende Bausteine** (aus Codeprüfung):
- `tts_audiobook_tool/text_ops/epub_extractor.py`: EPUB + Navigation
- `tts_audiobook_tool/project.py`: persistentes Project-Modell
- `tts_audiobook_tool/generate_util.py` und `generation_events.py`: Generierungs- und Fortschrittsereignisse
- `tts_audiobook_tool/server/server.py`: bisheriger REST-Server für Prompt/Streaming, **nicht** die gesuchte vollständige Hörbuch-API
- `browser_player/`: bestehender Audio/Text-Player

**Vorgeschlagene Ergänzungen:**
- Backend: FastAPI als separater, modularer App-Adapter, der **bestehende Projekt-/Generierungsroutinen** aufruft (nicht deren Logik duplizieren).
- Frontend: React, TypeScript, Vite, keine externen TTS-Anbieter vorausgesetzt.
- Status-/Auftragsverwaltung: eine persistierte lokale Job-Queue (zunächst ein GPU-Job), explizite Zustände, atomare Checkpoints und robuste Wiederaufnahme. Live-Status per SSE.
- Private Daten: `/workspace/audiobook-data`; keine Buchinhalte, Referenzaudios, Tokens in Git-Commits, öffentlich erreichbaren Pfaden, Analytics oder Browser-Logs.
- Exponierung: RunPod HTTPS-Proxy nur mit geeignetem **Zugriffsschutz**; die Web-App darf keine frei zugängliche ungeschützte TTS-/Dateiverwaltungs-API sein. Strikte sichere Dateinamen, Größenlimits, MIME/EPUB-Validierung und Pfadbegrenzung.
- Konfigurations-/Modellumgebung: Backend in passender Python-Umgebung starten oder den vorhandenen isolierten GPU-Worker anbinden; **Torch 2.8 (Qwen) und 2.9 (MOSS) nicht in denselben venv installieren**. Modell-Switching serialisieren.
- Original-TUI bleibt als Rückfallebene vorhanden; der Standard-RunPod-Befehl wechselt erst nach getesteter Browser-E2E-Freigabe auf Web-Start.

## 6. Lieferreihenfolge

1. **UI + sichere API-Grundlage:** Projektbibliothek, EPUB-Import/-Vorschau/-Startauswahl und deutsche Texte; technische TUI bleibt funktional.
2. **Kuratierte Stimmen:** Preset-Manifest + Lizenzprüfung + privates Beschaffen der Clips + Auswahlkarten mit deutschen Hörproben und festem Preset; internes Qwen/MOSS-QA.
3. **Job-/Audio-Workflow:** Generierung, Status, Retry/Resume, QA-Ansicht, M4B/MP3, vorhandener Player.
4. **RunPod-Integration:** geschützter Webzugriff, automatisierter Start, Gesundheitsprüfung, Reconnect, Volume-Persistenz und Browser-E2E-Tests.

## 7. Definition of Done

- Web-App ist vollständig auf Deutsch; deutscher Text wird standardmäßig **mit deutscher Modell-Sprachvorgabe** erzeugt.
- Es gibt **keine Stimmstudio-Seite und keine Upload-/Editierfunktion für Sprecherreferenzen**.
- Ein rechtlich freigegebenes männliches Referenzprofil ist mit deutscher Hörprobe wirklich nutzbar; weitere echte Sprecher erst nach eigener Rechts- und Hörprüfung.
- Das Hörbuch beginnt am bestätigten Vorwort und enthält alle gewünschten Kapitel in korrekter Reihenfolge.
- Fachbegriffe und Zahlen bestehen einen dokumentierten deutsch/englischen Aussprache-Test.
- Status, Fehler, Retry und Fortsetzung funktionieren nach Browser-Reload/Pod-Neustart.
- RunPod HTTPS ist authentifiziert; keine privaten Dateien im öffentlichen Repository oder ohne Zugriffskontrolle.
- End-to-End: EPUB hochladen -> Vorwort bestätigen -> fertiges Profil wählen -> deutsche Hörprobe -> Hörbuch -> M4B mit Kapitelmarken.
- Keine „fertig“-Meldung bevor Real-GPU-Inferenz und Hörproben geprüft wurden.

## Quellen für Sprecherprüfung

- Thorsten-Voice 44kHz: https://huggingface.co/datasets/Thorsten-Voice/TV-44kHz-Full (CC0; ein männlicher Sprecher)
- Originalsprecher zur Lizenz: https://www.thorsten-voice.de/ueber-das-projekt/aufnahmen/
- Qwen3-TTS Base cloning: https://github.com/QwenLM/Qwen3-TTS
- MOSS-TTS v1.5: https://github.com/OpenMOSS/MOSS-TTS
