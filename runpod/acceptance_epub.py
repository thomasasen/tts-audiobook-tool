"""Public-domain, purpose-written German EPUB used for offline and GPU QA.

The archive is built in memory. No licensed book is ever committed or copied
into CI. Text, spine, navigation and chapter ordering are fully deterministic.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def build_epub(*, repeat: int = 1) -> bytes:
    if not 1 <= repeat <= 50:
        raise ValueError("repeat must be between 1 and 50")
    sections = [
        ("toc", "Inhaltsverzeichnis", "Vorwort · Kapitel 1 · Kapitel 2."),
        ("foreword", "Vorwort", "Dieses Testbuch erklärt eine nachvollziehbare Entscheidung im Vertrieb. "
         "Es ist ein frei verfasster deutscher Prüfungstext."),
        ("chapter1", "Kapitel 1", "Eine gute Bedarfsanalyse erfasst Ziele, Probleme und Entscheidungswege. "
         "Die Kundin möchte achtzig Prozent ihrer Routinearbeit automatisieren."),
        ("chapter2", "Kapitel 2", "Im nächsten Gespräch prüfen wir den wirtschaftlichen Nutzen und die Risiken. "
         "Die Vereinbarung steht am Ende eines strukturierten Prozesses."),
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>'
    # Exercise more than ordinary paragraphs: nested headings, structured
    # lists, abbreviations, currency, punctuation and mixed-language terms.
    # All texts are independently authored and may be shared in CI.
    extras = {
        "chapter1": (
            "<h2>Messbare Kriterien</h2>"
            "<p>Im CRM-System sinkt die Bearbeitungszeit von 120 auf 85 Minuten. "
            "Das entspricht 29 Prozent. Das SaaS-Budget beträgt 2.500 Euro pro Monat.</p>"
            "<ul><li>Ziel: eine belastbare Pipeline.</li>"
            "<li>Kontrolle: ein monatliches Forecast-Review.</li>"
            "<li>Risiko: fehlende Datenqualität.</li></ul>"
        ),
        "chapter2": (
            "<h2>Gesprächsführung</h2>"
            "<p>Die Leiterin fragt: &#8222;Ist der Business Case für 2027 belastbar?&#8220; "
            "Der Kollege antwortet: &#8222;Ja, aber prüfen wir zunächst den ARR.&#8220;</p>"
            "<p>Eine klare Entscheidung braucht Zahlen, Verantwortung und Vertrauen!</p>"
        ),
    }
    xhtml = []
    for ident, heading, phrase in sections:
        paragraphs = "".join(f"<p>{escape(phrase)}</p>" for _ in range(repeat if ident != "toc" else 1))
        paragraphs += extras.get(ident, "")
        xhtml.append((ident, heading,
            f'{xml}<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{escape(heading)}</title></head>'
            f'<body><h1>{escape(heading)}</h1>{paragraphs}</body></html>'))
    nav_items = "".join(
        f'<li><a href="{ident}.xhtml">{escape(heading)}</a></li>'
        for ident, heading, _ in sections if ident != "toc"
    )
    nav = (f'{xml}<html xmlns="http://www.w3.org/1999/xhtml" '
           'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Navigation</title></head>'
           f'<body><nav epub:type="toc"><ol>{nav_items}</ol></nav></body></html>')
    manifest = "".join(
        f'<item id="{ident}" href="{ident}.xhtml" media-type="application/xhtml+xml"/>'
        for ident, _, _ in sections
    )
    spine = "".join(f'<itemref idref="{ident}"/>' for ident, _, _ in sections)
    opf = (f'{xml}<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="bookid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="bookid">urn:uuid:3c1f99b6-3589-4f59-a781-7f4080a5e344</dc:identifier>'
           '<dc:title>Prüfbuch für deutsche Hörbücher</dc:title><dc:language>de</dc:language>'
           '</metadata><manifest>'
           f'{manifest}<item id="nav" href="nav.xhtml" '
           'media-type="application/xhtml+xml" properties="nav"/>'
           f'</manifest><spine>{spine}</spine></package>')
    container = (f'{xml}<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        for ident, _, document in xhtml:
            zf.writestr(f"OEBPS/{ident}.xhtml", document)
    return memory.getvalue()

def build_complex_epub() -> bytes:
    """Build a self-authored EPUB for structural-loss and recovery regressions."""
    xml = '<?xml version="1.0" encoding="UTF-8"?>'
    docs = {
        "toc": (
            "Inhaltsverzeichnis",
            '<h1>Inhaltsverzeichnis</h1><ol>'
            '<li><a href="foreword.xhtml">Vorwort</a></li>'
            '<li><a href="chapter1.xhtml#part-a">Teil A</a></li>'
            '<li><a href="chapter1.xhtml#part-b">Teil B</a></li>'
            '<li><a href="chapter2.xhtml">Kapitel 2</a></li>'
            '<li><a href="chapter2.xhtml#formula">Formel</a></li>'
            '</ol>',
        ),
        "nav": (
            "Navigation",
            '<nav epub:type="toc"><ol>'
            '<li><a href="foreword.xhtml">Vorwort</a></li>'
            '<li><a href="chapter1.xhtml#part-a">Teil A</a>'
            '<ol><li><a href="chapter1.xhtml#part-b">Teil B</a></li></ol></li>'
            '<li><a href="chapter2.xhtml">Kapitel 2</a></li>'
            '</ol></nav><p>Kurzer Hinweis für alle Leser.</p>',
        ),
        "imprint": (
            "Impressum",
            '<h1>Impressum</h1><p>Copyright © 2026 Beispielverlag.</p>'
            '<p>Alle Rechte vorbehalten. ISBN 978-1-23456-789-0.</p>',
        ),
        "foreword": (
            "Vorwort",
            '<h1>Vorwort</h1><p>Dieses frei verfasste Vorwort erklärt den Zweck des Buches '
            'und führt in die folgenden fachlichen Abschnitte ein.</p>',
        ),
        "chapter1": (
            "Kapitel 1",
            '<h1>Kapitel 1</h1>'
            '<h2 id="part-a">Teil A</h2>'
            '<p>Die <strong>wichtige Kennzahl</strong> beschreibt einen <em>messbaren</em> '
            'Zusammenhang zwischen Aufwand und Ergebnis.</p>'
            '<table><caption>Umsatz nach Quartal</caption>'
            '<thead><tr><th>Quartal</th><th>Umsatz</th></tr></thead>'
            '<tbody><tr><td>Q1</td><td>1,2 Millionen Euro</td></tr>'
            '<tr><td>Q2</td><td>1,5 Millionen Euro</td></tr></tbody></table>'
            '<figure><img src="figure.png" alt="Balkendiagramm zum Umsatz"/>'
            '<figcaption>Abbildung 1: Der Umsatz steigt im zweiten Quartal.</figcaption></figure>'
            '<hr class="scene-break"/>'
            '<h2 id="part-b">Teil B</h2>'
            '<p>Die Berechnung berücksichtigt die folgende Reihenfolge. '
            'Eine Fußnotenmarke verweist auf zusätzliche Erläuterungen'
            '<a epub:type="noteref" href="#fn1">1</a>.</p>'
            '<ol start="3"><li>Erster geordneter Prüfschritt.</li>'
            '<li value="4">Zweiter geordneter Prüfschritt.'
            '<ul><li>Unterpunkt mit einer zusätzlichen Bedingung.</li></ul></li></ol>'
            '<aside id="fn1" epub:type="footnote" role="doc-footnote">'
            '<p>Fußnote 1: Diese Erläuterung gehört zum vorherigen Satz.</p></aside>',
        ),
        "chapter2": (
            "Kapitel 2",
            '<h1>Kapitel 2</h1><p>Die Formel beschreibt den Zusammenhang der Variablen.</p>'
            '<p id="formula">Die Gleichung lautet '
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo><mn>2</mn></math>.'
            '</p>',
        ),
    }
    manifest = []
    spine = []
    xhtml = {}
    for ident, (title, body) in docs.items():
        xhtml[ident] = (
            f'{xml}<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<head><title>{escape(title)}</title></head><body>{body}</body></html>'
        )
        properties = ' properties="nav"' if ident == "nav" else ""
        manifest.append(
            f'<item id="{ident}" href="{ident}.xhtml" media-type="application/xhtml+xml"{properties}/>'
        )
        spine.append(f'<itemref idref="{ident}"/>')
    manifest.append('<item id="figure" href="figure.png" media-type="image/png"/>')
    opf = (
        f'{xml}<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:6d39f452-2d62-47e0-9f3d-0b2ac6e3a22c</dc:identifier>'
        '<dc:title>Strukturtest für EPUB-Audits</dc:title><dc:language>de</dc:language>'
        '</metadata><manifest>' + ''.join(manifest) + '</manifest><spine>'
        + ''.join(spine) + '</spine></package>'
    )
    container = (
        f'{xml}<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    image = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
    )
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        for ident, document in xhtml.items():
            archive.writestr(f"OEBPS/{ident}.xhtml", document)
        archive.writestr("OEBPS/figure.png", image)
    return memory.getvalue()
