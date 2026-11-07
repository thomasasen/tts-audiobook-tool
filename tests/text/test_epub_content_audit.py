from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

from tts_audiobook_tool.text_ops.epub_content_audit import audit_epub_content


class Chapter:
    def __init__(self, href, text="Kapitel 1", source_spans=None):
        self.href = href
        self.title = "Kapitel 1"
        self.text = text
        self.source_spans = source_spans or []


def make_epub():
    buffer = io.BytesIO()
    container = b'''<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
    opf = b'''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>
      <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
      <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
      <item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
      <item id="cover" href="cover.jpg" media-type="image/jpeg"/>
    </manifest><spine><itemref idref="ch1"/><itemref idref="ch2"/></spine></package>'''
    chapter1 = """<html><body>
      <h1>Kapitel 1</h1><p>Ein <em>wichtiger</em> Absatz.</p>
      <table><caption>Messwerte</caption><tr><th>Jahr</th><th>Wert</th></tr><tr><td>2026</td><td>42</td></tr></table>
      <ul><li>Punkt eins</li></ul>
      <figure><img src="chart.png" alt="Diagramm der Entwicklung"/><figcaption>Entwicklung</figcaption></figure>
      <img src="logo.png" alt="Firmenlogo"/>
      <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo><mn>5</mn></math>
      <a role="doc-noteref" href="#fn1">1</a><aside role="doc-footnote" id="fn1">Eine wichtige Fußnote.</aside>
      <a role="doc-noteref" href="notes.xhtml#fn2">2</a>
      <hr/><div class="scene-break">* * *</div>
    </body></html>"""
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter1.xhtml", chapter1)
        archive.writestr("OEBPS/chapter2.xhtml", "<html><body><nav><p>Inhaltsverzeichnis</p></nav></body></html>")
        archive.writestr("OEBPS/notes.xhtml", '<html><body><aside role="doc-footnote" id="fn2">Eine ausgelagerte Fußnote.</aside></body></html>')
        archive.writestr("OEBPS/cover.jpg", b"image")
    return buffer.getvalue()


def test_audit_reports_markup_features_skips_review_elements_and_exact_text_mapping(tmp_path):
    path = tmp_path / "audit.epub"
    path.write_bytes(make_epub())
    text = "Kapitel 1"
    chapter = Chapter("OEBPS/chapter1.xhtml", text, [
        SimpleNamespace(
            source_href="OEBPS/chapter1.xhtml", source_start=0, source_end=len(text),
            output_start=0, output_end=len(text), navigation_title="Kapitel 1",
            mapping_status="mapped",
        )
    ])
    report = audit_epub_content(
        str(path),
        [chapter],
        ["Skipped navigation document"],
        skipped_sources=[{
            "href": "OEBPS/chapter2.xhtml", "kind": "navigation-document",
            "status": "skipped", "reason": "Navigation ohne Vorlesetext",
            "preview": "Inhaltsverzeichnis", "can_restore": False, "overridden": False,
        }],
    )
    assert report["version"] == 2
    assert report["status"] == "review-required"
    assert report["summary"]["spine_items"] == 2
    assert report["summary"]["retained_spine_items"] == 1
    assert report["summary"]["skipped_spine_items"] == 1
    assert report["summary"]["source_tables"] == 1
    assert report["summary"]["source_lists"] == 1
    assert report["summary"]["source_images"] == 2
    assert report["summary"]["source_captions"] == 2
    assert report["summary"]["source_mathml"] == 1
    assert report["summary"]["source_footnotes"] == 1
    assert report["summary"]["source_footnote_refs"] == 2
    assert report["summary"]["linked_non_spine_footnote_targets"] == 1
    assert report["summary"]["source_scene_breaks"] == 2
    assert report["summary"]["source_inline_formatting"] >= 1
    assert report["summary"]["mapping_coverage_percent"] == 100
    assert report["resources"][0]["preview"].startswith("Kapitel 1")
    assert any(item["href"] == "OEBPS/chapter2.xhtml" for item in report["skipped"])
    external_note = next(item for item in report["skipped"] if item.get("href") == "OEBPS/notes.xhtml")
    assert external_note["kind"] == "non-spine-footnote-target"
    assert external_note["can_restore"] is False
    assert "nicht automatisch" in external_note["reason"]
    elements = report["review_elements"]
    assert {item["element_key"] for item in elements} >= {"table:0", "figure:0", "math:0", "img:1"}
    assert next(item for item in elements if item["element_key"] == "figure:0")["preview"] == "Entwicklung"


def test_audit_exposes_approved_manual_speaker_text_without_generating_it(tmp_path):
    path = tmp_path / "audit.epub"
    path.write_bytes(make_epub())
    report = audit_epub_content(
        str(path),
        [Chapter("OEBPS/chapter1.xhtml")],
        speaker_overrides={"OEBPS/chapter1.xhtml": {"table:0": "Die Tabelle nennt den Wert 42."}},
    )
    table = next(item for item in report["review_elements"] if item["element_key"] == "table:0")
    assert table["speaker_text"] == "Die Tabelle nennt den Wert 42."
    assert table["approved"] is True
    assert "Die Tabelle nennt den Wert 42." not in table["source_text"]


def test_audit_handles_broken_container_without_failing_import(tmp_path):
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    report = audit_epub_content(str(path), [])
    assert report["status"] == "audit-error"
    assert report["warnings"]
