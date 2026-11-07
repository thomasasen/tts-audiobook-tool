"""B01 red-team regressions: wholly synthetic content, never private books."""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest

from tts_audiobook_tool.app_types import SegmentationStrategy
from tts_audiobook_tool.text_ops.epub_css import EpubCssResolver, parse_declarations, parse_stylesheet
from tts_audiobook_tool.text_ops.epub_extractor import (
    BeautifulSoupEpubChapterTextExtractor, EpubExtractor, EpubSourceChapter,
)
from tts_audiobook_tool.text_ops.epub_section_skip_detector import EpubSectionSkipDetector
from tts_audiobook_tool.text_ops.epub_content_audit import audit_epub_content


def _extract(html: str, css: str = ".float {float: left}") -> object:
    resolver = EpubCssResolver(parse_stylesheet(css))
    source = EpubSourceChapter("Kapitel", "chapter.xhtml", "application/xhtml+xml", html, css=resolver)
    return BeautifulSoupEpubChapterTextExtractor().extract_text(source)


@pytest.mark.parametrize(("initial", "tail", "expected"), [
    ("W", "ährend der Sitzung wurde diskutiert.", "Während der Sitzung wurde diskutiert."),
    ("D", "er Händler reiste nach Köln.", "Der Händler reiste nach Köln."),
    ("„W", "ir gehen heute nach München.“", "„Wir gehen heute nach München.“"),
    ("Ü", "bermorgen beginnt die Prüfung.", "Übermorgen beginnt die Prüfung."),
    ("Eine", "Woche später startete die Arbeit.", "Eine Woche später startete die Arbeit."),
    ("D", "Die Reise war erfolgreich.", "Die Reise war erfolgreich."),
])
def test_real_floated_initial_parsing(initial, tail, expected):
    html = f'<html><body><h2>Kapitel</h2><div class="float"><p>{initial}</p></div><p>{tail}</p></body></html>'
    result = _extract(html)
    assert expected in result.text
    assert not result.significant_warnings
    assert "\n" not in result.text.split("Kapitel", 1)[1].strip()


def test_drop_cap_preserves_navigation_anchor_from_floated_initial():
    from tts_audiobook_tool.text_ops.epub_extractor import EpubNavigationTarget
    target = EpubExtractor.make_navigation_target("chapter.xhtml#opening", "Erster Teil", 0)
    assert isinstance(target, EpubNavigationTarget)
    source = EpubSourceChapter(
        "Kapitel", "chapter.xhtml", "application/xhtml+xml",
        '<body><div class="float"><p id="opening"><em>W</em></p></div><p>ährenddessen ging es weiter.</p></body>',
        navigation_targets=[target],
        css=EpubCssResolver(parse_stylesheet(".float { float: left; }")),
    )
    result = BeautifulSoupEpubChapterTextExtractor().extract_text(source)
    assert "Währenddessen" in result.text
    assert not result.has_unresolved_navigation_target


def test_non_floated_blocks_and_unrelated_floats_are_not_concatenated():
    ordinary = _extract('<body><div><p>W</p></div><p>ährend eines normalen Absatzes.</p></body>')
    assert "Während" not in ordinary.text
    ambiguous = _extract('<body><div class="float"><p>D</p></div><p>Während einer Pause.</p></body>')
    assert "D" in ambiguous.text and "Während" in ambiguous.text
    assert any("manual review" in message for message in ambiguous.significant_warnings)
    after_heading = _extract('<body><div class="float"><p>W</p></div><h2>ährend</h2></body>')
    assert "Während" not in after_heading.text


def test_only_explicit_image_scene_separators_produce_markers():
    decorative = _extract(
        '<body><p>Erster Abschnitt.</p><p><img alt="break-dinkus-symbol" src="decorative.png"/></p>'
        '<p>Zweiter Abschnitt.</p></body>'
    )
    assert "* * *" in decorative.text
    logo = _extract(
        '<body><p>Erster Abschnitt.</p><p><img alt="Company logo" src="logo.png"/></p>'
        '<p>Zweiter Abschnitt.</p></body>'
    )
    assert "* * *" not in logo.text


def test_attribution_detection_requires_standalone_tool_credit():
    synthetic = (
        '<html><body><div class="attribution"><p>Übersetzt mit Beispiel-Tool</p>'
        '<p>https://example.invalid/tool</p></div></body></html>'
    )
    decision = EpubSectionSkipDetector.detect_technical_attribution_skip(
        "Text/attribution.xhtml", "Attribution", synthetic,
    )
    assert decision.should_skip
    assert not EpubSectionSkipDetector.detect_technical_attribution_skip(
        "Text/chapter.xhtml", "Kapitel", synthetic,
    ).should_skip
    assert not EpubSectionSkipDetector.detect_technical_attribution_skip(
        "Text/attribution.xhtml", "Übersetzung", '<body><p>Diese Übersetzung wurde geprüft.</p></body>',
    ).should_skip


def test_float_is_resolved_but_unrelated_css_is_not():
    assert parse_declarations("float: left; color: red") == {"float": "left"}
    assert parse_declarations("float: inline-start") == {}


def _make_synthetic_epub() -> bytes:
    """EPUB 2 with five reading chapters, an XHTML TOC, image and tool credit."""
    chapters = []
    for index in range(1, 6):
        body = (
            '<div class="float"><p id="start">W</p></div><p>ährend einer Besprechung'
            ' entstand ein neuer Gedanke.</p><p>Ein zweiter Absatz folgt.</p>'
            if index == 1 else
            ('<p>Text vor der Grafik.</p><p><img src="../chart.png" alt="Umsatzdiagramm"/></p>'
             '<p>Text nach der Grafik.</p>' if index == 2
             else f"<p>Dies ist der selbst verfasste Text von Abschnitt {index}.</p>")
        chapters.append(
            f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Kapitel {index}</title>'
            f'<link rel="stylesheet" type="text/css" href="../style.css"/></head>'
            f'<body><h1>{index}</h1>{body}</body></html>'
        )
    labels = [
        "1. Während der Besprechung", "2. Das Diagramm", "3. Die Planung",
        "4. Die Zusammenarbeit", "5. Die Zusammenfassung",
    ]
    contents = "".join(
        f'<li><a href="Text/ch{i}.xhtml#start">{escape(title)}</a></li>'
        for i, title in enumerate(labels, start=1)
    )
    # Fragments other than chapter 1 are omitted here because test navigation
    # matches paths and later imports should not require nonexistent anchors.
    contents = "".join(
        f'<li><a href="Text/ch{i}.xhtml">{escape(title)}</a></li>'
        for i, title in enumerate(labels, start=1)
    )
    toc_xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Inhaltsverzeichnis</title>'
        '</head><body><h1>Inhaltsverzeichnis</h1><ol>' + contents + "</ol></body></html>"
    )
    ncx_points = "".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{i}</text></navLabel>'
        f'<content src="Text/ch{i}.xhtml"/></navPoint>'
        for i in range(1, 6)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        '<head><meta name="dtb:uid" content="synthetic-b01"/></head><docTitle><text>Test</text></docTitle>'
        '<navMap>' + ncx_points + '</navMap></ncx>'
    )
    manifest = [
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="toc" href="toc.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="img" href="chart.png" media-type="image/png"/>',
    ] + [
        f'<item id="c{i}" href="Text/ch{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(1, 6)
    ] + ['<item id="attr" href="Text/attribution.xhtml" media-type="application/xhtml+xml"/>']
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="BookId">synthetic-b01</dc:identifier>'
        '<dc:title>Selbst verfasstes Testbuch</dc:title><dc:language>de</dc:language></metadata>'
        '<manifest>' + "".join(manifest) + '</manifest>'
        '<spine toc="ncx"><itemref idref="toc"/>' +
        "".join(f'<itemref idref="c{i}"/>' for i in range(1, 6)) +
        '<itemref idref="attr"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/toc.ncx", ncx)
        archive.writestr("OEBPS/toc.xhtml", toc_xhtml)
        archive.writestr("OEBPS/style.css", ".float { float: left }")
        archive.writestr("OEBPS/chart.png", b"synthetic image placeholder")
        for index, html in enumerate(chapters, start=1):
            archive.writestr(f"OEBPS/Text/ch{index}.xhtml", html)
        archive.writestr(
            "OEBPS/Text/attribution.xhtml",
            '<html><body><p>Übersetzt mit Beispiel-Tool.</p>'
            '<p>https://example.invalid/tool</p></body></html>',
        )
    return buffer.getvalue()


def test_canonical_synthetic_epub_import_navigation_attribution_and_overrides(tmp_path):
    pytest.importorskip("ebooklib.epub")
    path = tmp_path / "synthetic-b01.epub"
    path.write_bytes(_make_synthetic_epub())
    extracted = EpubExtractor.extract_import_chapters(str(path))
    titles = [chapter.title for chapter in extracted.chapters]
    assert len(titles) == 5
    assert titles[0] == "1. Während der Besprechung"
    assert titles[-1] == "5. Die Zusammenfassung"
    assert "Während einer Besprechung" in extracted.chapters[0].text
    assert any(item["kind"] == "technical-attribution" for item in extracted.skipped_sources)
    assert all("Beispiel-Tool" not in chapter.text for chapter in extracted.chapters)
    assert all("Text/ch" in span.source_href for chapter in extracted.chapters for span in chapter.source_spans)

    report = audit_epub_content(
        str(path), extracted.chapters, extracted.warnings,
        skipped_sources=extracted.skipped_sources,
    )
    assert report["summary"]["source_images"] == 1
    assert any(item["kind"] == "Bild" for item in report["review_elements"])

    speaker_text = "Die Abbildung zeigt eine selbst erstellte Übersicht."
    modified = EpubExtractor.extract_import_chapters(
        str(path), speaker_overrides={"Text/ch2.xhtml": {"img:0": speaker_text}},
    )
    chapter = modified.chapters[1].text
    assert chapter.index("Text vor der Grafik") < chapter.index(speaker_text) < chapter.index("Text nach der Grafik")
    assert "Umsatzdiagramm" not in chapter
    assert speaker_text not in extracted.chapters[1].text

    restored = EpubExtractor.extract_import_chapters(
        str(path), include_skipped_hrefs={"Text/attribution.xhtml"},
    )
    assert len(restored.chapters) == 6
    assert "Beispiel-Tool" in restored.chapters[-1].text
    assert "Beispiel-Tool" not in restored.chapters[-2].text

    imported = EpubExtractor.import_epub(
        str(path), max_words=40,
        segmentation_strategy=SegmentationStrategy.SENTENCE_PLUS,
        language_code="de",
        speaker_overrides={"Text/ch2.xhtml": {"img:0": speaker_text}},
    )
    assert len(imported.chapters) == 5
    assert len(imported.phrase_groups) >= 5
    assert speaker_text in imported.raw_text
    assert "Beispiel-Tool" not in imported.raw_text


def test_conflicting_xhtml_toc_cannot_override_navigation_labels():
    class Item:
        file_name = "toc.xhtml"
        content = (
            '<html><body>' +
            "".join(f'<a href="chapter{i}.xhtml">Titel {i}</a>' for i in (5, 4, 3, 2, 1)) +
            "</body></html>"
        )
        def get_content(self):
            return self.content.encode()

    class Book:
        items = [Item()]

    targets = [
        EpubExtractor.make_navigation_target(f"chapter{i}.xhtml", str(i), i)
        for i in range(1, 6)
    ]
    assert all(target is not None for target in targets)
    unchanged = EpubExtractor.prefer_matching_xhtml_toc_labels(Book(), targets)
    assert [target.title for target in unchanged] == [str(i) for i in range(1, 6)]
