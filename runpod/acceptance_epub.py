"""Public-domain, purpose-written German EPUB used for offline and GPU QA.

The archive is built in memory. No licensed book is ever committed or copied
into CI. Text, spine, navigation and chapter ordering are fully deterministic.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def build_epub(*, repeat: int = 1, include_audit_cases: bool = False) -> bytes:
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
    if include_audit_cases:
        sections.insert(1, (
            "copyright", "Copyright",
            "Copyright © 2025 Example Press. All rights reserved. ISBN 978-1-2345-6789-0.",
        ))
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
    if include_audit_cases:
        extras["chapter1"] += (
            "<table><caption>Messwerte</caption><tr><th>Jahr</th><th>Wert</th></tr>"
            "<tr><td>2026</td><td>42</td></tr></table>"
            '<figure><img src="chart.svg" alt="Diagramm mit Umsatzverlauf"/>'
            "<figcaption>Umsatzentwicklung</figcaption></figure>"
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo><mn>5</mn></math>'
            '<a role="doc-noteref" href="#fn1">1</a>'
            '<aside role="doc-footnote" id="fn1">Die Messwerte stammen aus dem Beispiel.</aside>'
            '<hr/><div class="scene-break">* * *</div>'
            '<img src="chart.svg" alt="Firmenlogo"/>'
        )
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
        if include_audit_cases:
            zf.writestr(
                "OEBPS/chart.svg",
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 10 L5 0 L10 10"/></svg>',
            )
    return memory.getvalue()
