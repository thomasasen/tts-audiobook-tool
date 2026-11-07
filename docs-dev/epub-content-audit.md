# EPUB-Content-Audit und Sprecherfassungen

## Zweck

Der Content-Audit ergänzt den kanonischen EbookLib-/BeautifulSoup-Import. Er verändert weder die hochgeladene EPUB-Datei noch das Project-V5-/book.v3-Format. Er macht sichtbar, welche XHTML-Spine-Ressourcen in den Vorlesetext eingeflossen sind, welche Heuristiken Inhalte ausgelassen haben und welche komplexen Markup-Elemente eine menschliche Prüfung verdienen.

## Quelltext-zu-Vorlesetext-Mapping

Jede extrahierte Textspanne erhält:

- `source_href`: Archivpfad der XHTML-Quelldatei.
- `source_start` / `source_end`: Zeichenoffsets in der normalisierten, aus dieser Quelldatei extrahierten Textfolge.
- `output_start` / `output_end`: Zeichenoffsets in der resultierenden logischen Vorlesekapitelfassung.
- `navigation_title`: zugeordneter Navigations-/Abschnittstitel, sofern vorhanden.
- `mapping_status`: `mapped` oder `unresolved`.

Die Offsets sind keine ZIP-Bytepositionen, UTF-8-Byteoffsets oder DOM-Node-Positionen. Sie beziehen sich auf die normalisierte Extraktionsfolge. Bei manuell freigegebenen Sprecherfassungen enthält diese Folge den freigegebenen Text anstelle des ersetzten Elements.

Die Abdeckungsquote zählt Nicht-Leerzeichen im finalen Kapiteltext, die einer erfolgreich aufgelösten Quellspanne zugeordnet sind. Trennzeichen zwischen Abschnitten werden nicht als Quellinhalt gezählt. Eine Quote von 100 Prozent belegt daher die Zuordnung des übernommenen Textes, nicht die semantische Vollständigkeit von Bildern, Tabellen, Formeln oder Fußnoten.

## Auslassungen

Der Parser meldet übersprungene Spine-Ressourcen mit:

- Archivpfad und Titel,
- Skip-Kategorie und erklärendem Grund,
- kurzer Textvorschau,
- Status und Kennzeichen, ob die Heuristik manuell übersteuerbar ist.

Publication-Metadata- und TOC-Heuristiken können im Projektstatus `Entwurf` manuell übersteuert werden. Die Original-EPUB-Datei bleibt unverändert. Navigation-only-Dokumente, fehlende Ressourcen und nicht dekodierbare Inhalte werden nicht als wiederherstellbare Lesekapitel angeboten. Ein manuell aufgenommener Abschnitt muss weiterhin durch den kanonischen Extraktor sprechbaren Text liefern.

Nach dem Speichern der Kapitelauswahl sind Quellenänderungen gesperrt, damit keine bereits vorbereiteten Project-V5-Daten unbemerkt von einer neuen Kapitelreihenfolge abweichen.

## Komplexe Elemente und Sprecherfassungen

Der Audit inventarisiert Tabellen, MathML-Formeln, Figuren und eigenständige Bilder. Für jedes Element zeigt die Oberfläche vorhandene Quellinformationen und die aktuelle Extraktionsbehandlung.

Eine Sprecherfassung ist ausschließlich eine manuell eingegebene und explizit gespeicherte Formulierung. Es wird kein Inhalt automatisch aus einer Grafik oder Formel erfunden. Bei Übernahme ersetzt die Sprecherfassung das betreffende Quell-Markup in der Text-Extraktion:

- Tabelle: ersetzt die abgeflachten Zell- und Caption-Texte.
- MathML: ersetzt die abgeflachten Formel-Textknoten.
- Figure: ersetzt Abbildung und enthaltene Bildunterschrift.
- Eigenständiges Bild: fügt die Sprecherfassung an der Bildposition ein.

Die Sprecherfassung wird als Projektmetadatum in `chapters.json` gespeichert und beim anschließenden Project-V5-Import erneut durch denselben kanonischen Extraktor angewendet. Die EPUB-Quelldatei wird nicht verändert. Navigation-Anker innerhalb ersetzter Elemente bleiben auf der Einfügeposition verortet.

Die Element-IDs sind tagbezogene Dokumentreihenfolgen (`table:0`, `math:0`, `figure:0`, `img:1`). Sie sind für die unveränderte, projektintern gespeicherte EPUB-Quelldatei stabil; sie sind keine globalen IDs über verschiedene EPUB-Versionen hinweg.

## Regressionstests

`runpod.acceptance_epub.build_epub(include_audit_cases=True)` erzeugt ein vollständig selbst verfasstes Test-EPUB mit:

- einer heuristisch übersprungenen Copyright-Seite,
- Tabelle, Bild, Figure, MathML,
- Fußnotenverweis und Fußnotentext,
- horizontalem Trenner und explizitem Scene-Break-Markup.

Der E2E-Test prüft Quellenwiederaufnahme und Rücknahme, Mapping-Abdeckung, die manuelle Sprecherfassung für jedes Element sowie deren tatsächliche Übernahme in die gespeicherten Project-V5-Phrase-Groups. Weitere Unit-Tests prüfen Fragmentnavigation, Quell-Offsets, Mischdokumente mit Navigation und Prosa in Nicht-Paragraph-Containern.

## Manuelle Abnahme mit einem privaten EPUB

Vor dem Abschluss von B01 muss mindestens ein komplexes, rechtmäßig nutzbares privates EPUB im Studio geprüft werden:

1. Kapitelreihenfolge, Vorwort und Vorlesebeginn mit dem EPUB-Reader vergleichen.
2. Erste und letzte Absätze jedes Kapitels sowie Übergänge zwischen Navigationsankern prüfen.
3. Tabellen, Formeln, Bildunterschriften, Alternativtexte, Fußnoten und literarische Trennelemente kontrollieren.
4. Jede als übersprungen markierte Ressource fachlich einordnen; echte Fehlklassifikationen manuell aufnehmen.
5. Sprecherfassungen einzeln gegen die Quellgrafik/-tabelle/-formel prüfen und anschließend die Textvorschau kontrollieren.
6. Nach dem Speichern die Project-V5-Phrase-Groups mit der Projektvorschau vergleichen.

Eine grüne CI oder eine 100-Prozent-Zeichenabdeckung ersetzt diese redaktionelle Abnahme nicht.
