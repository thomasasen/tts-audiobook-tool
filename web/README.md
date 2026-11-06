# German Audiobook Studio: Web-Frontend und Produktions-API

**Stand:** Die deutschsprachige React/FastAPI-Anwendung ist implementiert und über
die bestehenden Pull Requests #8–#13 in Entwicklung. Die technische
Browser-/EPUB- und M4B-Abnahme läuft in GitHub Actions. **Reale GPU-Inferenz,
deutsche Hörabnahme und Pod-Neustart sind noch nicht freigegeben.**

## Architektur und Funktionen

- Geschützte Einzelbenutzer-Bibliothek mit privatem SQLite-Speicher
- EPUB-Import über den bestehenden EbookLib-/Projektparser, kein Parallelparser
- Vorwortauswahl, Ausschluss des Inhaltsverzeichnisses, Textvorschau
- Originales Project-V5-Buchformat und Ausspracheglossar
- Thorsten-CosyVoice3 als einzige kuratierte deutsche Männerstimme
- Echte Hörprobe mit expliziter manueller Freigabe, ohne Stimmstudio oder Upload
- Persistenter serieller GPU-Worker, Pause/Resume/Abbruch und Segmentwiederholung
- Optionale deutsche Whisper-ASR-Hinweise, WAV-Vorschau und M4B-AAC-Export
- Alte Terminaloberfläche bleibt weiterhin unabhängig nutzbar

Die Web-API ist **kein separater TTS-Modellklon** und darf die Projektdateien
nicht direkt aus einem anderen Benutzerprozess verändern. Modellgewichte,
Stimmen, Dateien und Zugangsschlüssel gehören auf ein persistentes privates
Volume außerhalb von Git.

## Lokal starten

Verwende eine bereits installierte vollständige Python-Projektumgebung (z. B.
Qwen). Alle Abhängigkeiten aus dem Originalprojekt müssen vorhanden sein;
nur web/requirements.txt reicht für EPUB-Projektpersistenz **nicht** aus.

~~~bash
python -m pip install -r web/requirements.txt
cd web && npm install && npm run build && cd ..
export AUDIOBOOK_STUDIO_DATA_DIR="$PWD/.studio-private"
export AUDIOBOOK_STUDIO_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(36))')"
python -m uvicorn tts_audiobook_tool.web_studio.app:app --host 127.0.0.1 --port 7860 --workers 1
~~~

Lokale Adresse: http://127.0.0.1:7860

Das Zugangstoken gehört weder in URLs noch in öffentliche Logs. Private
Dateien nicht im Git-Checkout ablegen; das Beispiel-Datenverzeichnis ist
nur für lokale Entwicklung gedacht. Vite-Entwicklung erfolgt über
den lokalen Dev-Server, niemals ungeschützt im Internet.

## RunPod-Schnellstart

Der [RunPod-Startbefehl](../README.md) konfiguriert den Webserver zusätzlich
zur ursprünglichen TUI, bindet Port 7860 an den RunPod-HTTPS-Proxy und
installiert Thorsten in einer getrennten Python-3.11-Umgebung. Ein starker
Bearer-Token bleibt verpflichtend; der Proxy ersetzt keine zusätzliche
Netzwerkbeschränkung.

Die komplette, auf echten Hardware-Einsatz ausgerichtete
[Abnahmecheckliste](../runpod/ACCEPTANCE.md) enthält Kopierbefehle,
GPU-Preflight, originales Test-EPUB, M4B-Kapitelprüfung,
Wiederanlaufprüfung und Hörkriterien.

## Geschützte Endpunkte

- GET /api/session: Token, Sprachausgabe- und Instanzkennzeichen
- GET/POST /api/projects: Projekte und EPUB-Import
- GET/PUT /api/projects/{id}/chapters: Start- und Kapitelwahl
- GET/PUT /api/projects/{id}/glossary: bestehende Word Substitutions
- GET /api/voices, POST /api/voices/preview und /approve: geprüfte Stimme
- GET/POST /api/projects/{id}/jobs: persistente Produktionsaufträge
- POST /jobs/{id}/control: Pause, Resume und Abbruch
- GET /jobs/{id}/quality, POST /segments/{index}/retry: ASR-QA und Korrektur
- POST /jobs/{id}/export, GET /jobs/{id}/download: M4B
- DELETE /api/projects/{id}: sichere Löschung nach Jobstopp

Sämtliche personenbezogenen bzw. dateibezogenen Endpunkte prüfen Bearer-Auth.
Die öffentliche Health-Route enthält keine privaten Daten.

## Sicherheit und Grenzen

Ein vertrauenswürdiger Administrator, **ein** Webserverprozess, **ein**
GPU-Worker. Kein echter Multiuser-/Clusterbetrieb, keine allgemein
zugängliche Bibliothek und keine automatische Freigabe ungeprüfter Stimmen.
Ein erfolgreiches CI-Ergebnis ist weder eine reale RunPod-GPU-Prüfung
noch ein Nachweis der natürlichen deutschen Hörbuchqualität.
