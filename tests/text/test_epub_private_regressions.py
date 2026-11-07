"""Synthetic B01 regressions. Never copy private EPUB files into this module."""
from __future__ import annotations

from runpod.acceptance_epub import build_epub
from tts_audiobook_tool.text_ops.epub_extractor import EpubExtractor
from tts_audiobook_tool.app_types import SegmentationStrategy


def test_technical_translation_attribution_is_reported_and_reversible(tmp_path):
    epub_path = tmp_path / "public-test.epub"
    epub_path.write_bytes(build_epub(include_attribution_case=True))

    baseline = EpubExtractor.extract_import_chapters(str(epub_path))
    assert any(
        item["kind"] == "technical-attribution" and item["can_restore"]
        for item in baseline.skipped_sources
    )
    assert all("Übersetzt mit einem lokalen Prüfwerkzeug" not in ch.text for ch in baseline.chapters)

    restored = EpubExtractor.extract_import_chapters(
        str(epub_path), include_skipped_hrefs={"technical-attribution.xhtml"},
    )
    assert any("Übersetzt mit einem lokalen Prüfwerkzeug" in ch.text for ch in restored.chapters)
    assert any(
        item["kind"] == "technical-attribution" and item["overridden"]
        for item in restored.skipped_sources
    )

    again = EpubExtractor.extract_import_chapters(str(epub_path))
    assert all("Übersetzt mit einem lokalen Prüfwerkzeug" not in ch.text for ch in again.chapters)


def test_technical_attribution_stays_out_of_project_phrase_groups(tmp_path):
    epub_path = tmp_path / "public-test.epub"
    epub_path.write_bytes(build_epub(include_attribution_case=True))
    kwargs = dict(
        epub_path=str(epub_path),
        max_words=60,
        segmentation_strategy=SegmentationStrategy.SENTENCE_PLUS,
        language_code="de",
    )

    baseline = EpubExtractor.import_epub(**kwargs)
    assert "Übersetzt mit einem lokalen Prüfwerkzeug" not in baseline.raw_text
    assert baseline.phrase_groups

    restored = EpubExtractor.import_epub(
        **kwargs, include_skipped_hrefs={"technical-attribution.xhtml"},
    )
    assert "Übersetzt mit einem lokalen Prüfwerkzeug" in restored.raw_text
    assert restored.phrase_groups
