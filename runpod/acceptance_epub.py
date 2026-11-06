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
    xhtml = []
    for ident, heading, phrase in sections:
        paragraphs = "".join(f"<p>{escape(phrase)}</p>" for _ in range(repeat if ident != "toc" else 1))
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
