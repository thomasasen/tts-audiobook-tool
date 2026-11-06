# Abnahmeprotokoll: German Audiobook Studio auf RunPod

**Entwicklungsstand, keine behauptete GPU-Freigabe.** Diese Prüfung ist verbindlich **vor einer produktiven Freigabe**, auch wenn der Entwicklungsstand zuvor mit ausdrücklicher Eigentümerfreigabe nach `master` integriert wurde. Bücher, WAVs, M4Bs und Tokens bleiben auf dem privaten RunPod-Volume. CI erzeugt und speichert keine Screenshot- oder Hörbucharchive.

## A. Mindestkonfiguration

- RunPod-Linux mit NVIDIA-GPU, funktionierendem nvidia-smi und persistentem Network Volume auf /workspace (mindestens 100 GB, bei mehreren Modellen 150–200 GB).
- Günstiger Einzelstimmen-Test: RTX 3090, 24 GB. Bei mehreren Modellen mehr Spielraum: A40/A6000, 48 GB. Ob eine GPU kosteneffizienter ist, muss anhand echter RTF-Messungen beurteilt werden.
- Qwen-Pythonumgebung aus dem Originalprojekt, zusätzliche isolierte Python-3.11-Thorsten-Umgebung.
- Weboberfläche über RunPod HTTPS auf Port 7860, mit mindestens 32 Zeichen langem Zugangsschlüssel und zusätzlicher Netzwerkabsicherung.
- Der vollständige Docker-Startbefehl steht in der obersten README.

## B. Manuelle Sprecherfreigabe

Im privaten RunPod-Terminal:

~~~bash
cd /workspace/tts-audiobook-tool
bash runpod/setup.sh --models qwen
bash runpod/install-thorsten.sh
tmux ls
cat /workspace/audiobook-data/studio-access-token
~~~

Webstudio über RunPod Connect → HTTP 7860 öffnen und anmelden. Unter **Sprecher** echte Thorsten-CosyVoice3-Hörprobe erzeugen, vollständig anhören und **nur bei akzeptabler natürlicher deutscher Aussprache** freigeben. Kein automatischer Klon, keine amerikanische Ersatzstimme, kein Stimmstudio.

Die veröffentlichten Installationsvorgaben stammen von Thorsten-Voice/CosyVoice3 auf Hugging Face, mit CosyVoice-Framework-Commit ace7c47 und Apache-2.0-Modelllizenz.

## C. Reale EPUB-zu-M4B-Abnahme auf GPU

~~~bash
cd /workspace/tts-audiobook-tool
PY=/workspace/audiobook-data/venvs/qwen/bin/python
"$PY" runpod/acceptance.py preflight
"$PY" runpod/acceptance.py start --repeat 3
"$PY" runpod/acceptance.py status
# Nach dem Status generated:
"$PY" runpod/acceptance.py finish
~~~

Das Skript sendet ein eigens verfasstes gültiges deutsches EPUB über die **echte authentifizierte API**, wählt das Vorwort als ersten vorzulesenden Abschnitt, speichert das bestehende Project-V5-Buchformat, erzeugt echte GPU-Audiosegmente, exportiert M4B, liest Kapitelmarken und decodiert AAC. Die Ergebnisse werden privat unter /workspace/audiobook-data/acceptance gespeichert.

**Auch nach einem technischen Bestehen muss das ausgegebene M4B angehört werden.** Insbesondere Vorwort, mehrere Stellen in beiden Kapiteln, Eigennamen, Zahlen, Satzpausen, Sprachstabilität, Auslassungen und Knackartefakte kontrollieren. Whisper-ASR meldet Verdachtsfälle, ersetzt den Hörtest aber nicht.

## D. Recovery-Test mit echtem Dienst- oder Pod-Neustart

Für den Neustarttest einen neuen längeren Prüflauf starten. Einen abgeschlossenen Kurztest vorab sicher als erledigt beenden.

~~~bash
"$PY" runpod/acceptance.py start --repeat 35
"$PY" runpod/acceptance.py status --checkpoint
~~~

Der Checkpoint ist erst nach mindestens einem erzeugten Segment möglich. **Während die Produktion noch läuft**, die Web-tmux-Sitzung bzw. den Pod im RunPod-Dashboard tatsächlich neu starten. Anschließend:

~~~bash
cd /workspace/tts-audiobook-tool
PY=/workspace/audiobook-data/venvs/qwen/bin/python
"$PY" runpod/acceptance.py status --verify-restart
"$PY" runpod/acceptance.py status
"$PY" runpod/acceptance.py finish
~~~

Die Prüfung verlangt eine neue, nur authentifiziert lesbare Server-Instanz-ID, mindestens den zuvor gesicherten Fortschritt und keinen Abbruchzustand. Das alleinige Aktualisieren der Website zählt nicht. Der fertige M4B-Export muss vollständig sein.

## E. GPU-Benchmark

Die neue Produktion hält pro Segment die wirkliche Inferenzdauer fest. Der aktuelle Segment-Zeitstempel umfasst auch eventuelle Modelllade- und Wiederholungszeiten und ist deshalb zunächst eine **Generierungs-/Segment-Walltime**, keine sauber isolierte reine GPU-Inferenzzeit. Für einen belastbaren Real-Time-Factor (RTF) müssen reine Inferenzzeit und Audiodauer getrennt erfasst werden. Werte kleiner 1 bedeuten schneller als Echtzeit. Separat erfasste Gesamtzeit enthält gegebenenfalls manuelle Unterbrechungen, Neustarts und Quality Checks und ist **kein** sauberer RTF. VRAM-Angaben aus Preflight/Abschluss sind Momentaufnahmen, keine gemessenen Spitzenwerte.

Ohne echten GPU-Lauf dürfen weder Zeit-, Kosten-, Qualitäts- noch RTF-Werte als bestanden bezeichnet werden.

## F. Vergleich Qwen3, MOSS und Thorsten

Nur mit rechtlich nutzbaren deutschen Sprecherprofilen und identischen selbst verfassten Prüfabsätzen vergleichen. Hörproben als A/B/C anonymisieren, bevor Personen sie beurteilen.

| Kriterium | Gewicht |
| --- | ---: |
| Natürlichkeit und Prosodie | 30 % |
| Deutsche Aussprache | 25 % |
| Fachbegriffe und englische Begriffe | 20 % |
| Stimmkonsistenz | 15 % |
| Audioartefakte und Segmentübergänge | 10 % |

Der vollständige reproduzierbare Testtextkatalog und das Blindhörprotokoll stehen in [VOICE_EVALUATION.md](VOICE_EVALUATION.md) und [voice_testset.json](voice_testset.json). Bei gravierenden Qualitätsproblemen oder ungeklärten Stimmenrechten ist die Abnahme unabhängig von der Gesamtnote gesperrt.

## G. Release-Gates

1. Python-/echte EPUB-/Chromium-Tests, M4B-Decode und RunPod-Syntax grün.
2. Thorsten installiert, reale deutsche Hörprobe abgenommen.
3. Echter RunPod-EPUB→M4B-Test mit vollständigen Kapiteln erfolgreich.
4. Pod-/Webserver-Neustart mit belegtem Wiederanlauf und fehlendem Datenverlust.
5. Fachbuch-Hörabnahme einschließlich ASR-Verdachtsfällen dokumentiert.
6. Keine privaten Buchdateien, Tokens, Stimmenreferenzen oder großen CI-Artefakte in Git.
7. PRs #8 bis #13 in korrekter Reihenfolge integrieren, das separate PR #6 gegen die übernommene Variante prüfen; keine ungetesteten Merges.

Die privaten JSON-Prüfprotokolle enthalten keine Nutzerbuchtexte und keine Zugangsdaten; die erzeugten WAV/M4B-Dateien bleiben ausschließlich auf dem Volume.
