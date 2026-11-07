from __future__ import annotations

import io
import zipfile

from tts_audiobook_tool.text_ops.epub_content_audit import audit_epub_content


class Chapter:
    def __init__(self, href):
        self.href = href


def make_epub():
    buffer = io.BytesIO()
    container = b'''<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
    opf = b'''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>
      <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
      <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
      <item id="cover" href="cover.jpg" media-type="image/jpeg"/>
    </manifest><spine><itemref idref="ch1"/><itemref idref="ch2"/></spine></package>'''
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter1.xhtml", """<html><body><h1>Kapitel 1</h1><p>Ein <em>wichtiger</em> Absatz.</p><table><caption>Messwerte</caption><tr><td>42</td></tr></table><ul><li>Punkt eins</li></ul><figure><img src="x.jpg" alt="Diagramm der Entwicklung"/><figcaption>Entwicklung</figcaption></figure></body></html>""")
        archive.writestr("OEBPS/chapter2.xhtml", "<html><body><nav><p>Inhaltsverzeichnis</p></nav></body></html>")
        archive.writestr("OEBPS/cover.jpg", b"image")
    return buffer.getvalue()


def test_audit_reports_spine_markup_features_and_unmatched_sources(tmp_path):
    path = tmp_path / "audit.epub"
    path.write_bytes(make_epub())
    report = audit_epub_content(str(path), [Chapter("OEBPS/chapter1.xhtml")], ["Skipped navigation document"])
    assert report["version"] == 1
    assert report["status"] == "review-required"
    assert report["summary"]["spine_items"] == 2
    assert report["summary"]["retained_spine_items"] == 1
    assert report["summary"]["skipped_spine_items"] == 1
    assert report["summary"]["source_tables"] == 1
    assert report["summary"]["source_lists"] == 1
    assert report["summary"]["source_images"] == 1
    assert report["summary"]["source_captions"] == 2
    assert report["resources"][0]["preview"].startswith("Kapitel 1")
    assert any(item["href"] == "OEBPS/chapter2.xhtml" for item in report["skipped"])


def test_audit_handles_broken_container_without_failing_import(tmp_path):
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    report = audit_epub_content(str(path), [])
    assert report["status"] == "audit-error"
    assert report["warnings"]
