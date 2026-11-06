# German Audiobook Studio – Web Phase 1

Die **erste Implementierungsstufe** bietet eine authentifizierte Bibliothek mit EPUB-Import, Kapitelanalyse und expliziter Startauswahl. **Keine TTS-Generierung, keine Hörprobe und kein M4B-Export sind in diesem Stand freigegeben.** Die vorhandene TUI und RunPod-Startbefehle bleiben unverändert.

## Lokal starten

Die vorhandene Python-Umgebung muss die Basisabhängigkeiten des Originalprojekts inklusive `ebooklib`, `beautifulsoup4`, `pysbd` und deren weiterführende Abhängigkeiten enthalten. Zusätzliche Web-Abhängigkeiten:

```bash
python -m pip install -r web/requirements.txt
cd web && npm install && npm run build && cd ..
export AUDIOBOOK_STUDIO_DATA_DIR="$PWD/.studio-private"
export AUDIOBOOK_STUDIO_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(36))')"
uvicorn tts_audiobook_tool.web_studio.app:app --host 127.0.0.1 --port 7860
```

Die Adresse ist lokal **http://127.0.0.1:7860**. Den Wert aus `AUDIOBOOK_STUDIO_TOKEN` beim Anmelden eingeben. Der Token darf nicht in URL, Git-Repository oder öffentliche Logs gelangen.

Für Frontend-Entwicklung kann `cd web && npm run dev` verwendet werden; Vite leitet `/api` auf den lokalen Backend-Port 7860 weiter. **Den Entwicklungsserver nicht über das öffentliche Netzwerk freigeben.**

## RunPod (noch nicht produktiv freigegeben)

Nach Prüfung von PR #6 kann der Backend-Port 7860 geschützt über den RunPod-HTTPS-Proxy bereitgestellt werden. **Vorher** ein starkes `AUDIOBOOK_STUDIO_TOKEN` als Pod-Secret/Umgebungsvariable und ein persistentes `AUDIOBOOK_STUDIO_DATA_DIR=/workspace/audiobook-data` konfigurieren. Die Web-App erzwingt einen Bearer-Token für private API-Routen, aber sie ist **kein Ersatz für eine vorgeschaltete Zugriffsbeschränkung**. Proxy-Zugriff, TLS, Isolation und Neustart sind auf einem echten Pod zu prüfen.

Die reguläre TUI bleibt der Standard; es erfolgt noch **keine** Umstellung des RunPod-Startbefehls. Externe öffentliche GitHub-Links verweisen auf Code, nicht auf eine live laufende Webinstanz.

## API-Grundlage

- `GET /api/health`: öffentliche Betriebsprüfung ohne private Daten.
- `GET /api/session`: Token-Validierung.
- `GET /api/projects`, `POST /api/projects`: geschützte Bibliothek und EPUB-Upload.
- `GET /api/projects/{id}`: Kapitel und Textvorschau.
- `PUT /api/projects/{id}/chapters`: Auswahl speichern, vorhandenes Project-V5-Format benutzen.
- `GET /api/voices`: nur geprüfte Profile wären verfügbar; Thorsten ist derzeit Kandidat.
- `GET /api/capabilities`: überprüfbarer Implementierungsstatus.

Upload-Prüfungen: 60 MB komprimierte Dateigröße, 300 MB unkomprimiert, 5000 Archiveinträge, kein Traversal, keine Symlinks, keine Verschlüsselung, EPUB-MIME im Archiv. Aufbewahrung: `studio-projects/{uuid}/source.epub`, private Kapitelvorschau und `engine/` mit den Originalformaten. Es werden keine Buchdateien im öffentlichen Git abgelegt.

## Grenzen und QA

Die Web-Tests prüfen Authentifizierung, Archivabwehr, Kapitelvorschau, deutsche Standardauswahl und das Fail-Closed-Verhalten ohne Sprecher. Sie verwenden für die Import-API eine kontrollierte **Extractor-Test-Double**: das beweist nicht, dass der gesamte Original-Importer mit einem echten Fachbuch funktioniert. Echtes EPUB-E2E, Vorwort-Abgleich, Qwen/MOSS-Inferenz, stimmrechtliche Prüfung, Audioqualitätsvergleich, Weiterlaufen nach Pod-Neustart und M4B-Kapitelmarken stehen aus.

Prüfe:
```bash
python -m pytest tests/web/test_studio_api.py -q
cd web && npm run build
```

**Sicherheits- und Betriebsgrenzen:** Einzelbenutzer-Modus, ein Webserver-Prozess, nur ein vertrauenswürdiger Administrator. Keine freigegebene Mehrnutzer-/Multiworker-Jobverwaltung; keine parallele GPU-Job-Ausführung. Auch nach bestandenem Frontend-Build ist eine Browser-Abnahme nötig.

Die verbindliche Gesamtanforderung steht in [docs/web-ui-german-spec.md](../docs/web-ui-german-spec.md) (PR #8).
