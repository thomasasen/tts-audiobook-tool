"""German EPUB frontmatter detection regression tests; no private book assets."""
from types import SimpleNamespace

from tts_audiobook_tool.text_ops.epub_section_skip_detector import EpubSectionSkipDetector as Skip
from tts_audiobook_tool.web_studio.app import _catalogue


def _toc_html(title: str) -> str:
    links = "".join(f'<li><a href="kapitel-{i}.xhtml">Kapitel {i}</a></li>' for i in range(1, 7))
    return f"<html><body><h1>{title}</h1><ol>{links}</ol></body></html>"


def test_german_toc_recognized_by_strong_title_and_link_structure():
    result = Skip.detect_table_of_contents_skip(
        readable_spine_index=1, readable_spine_count=12,
        href="OEBPS/inhaltsverzeichnis.xhtml", title="Inhaltsverzeichnis",
        html=_toc_html("Inhaltsverzeichnis"),
    )
    assert result.should_skip


def test_german_inhaltsuebersicht_umlaut_normalized():
    assert Skip.normalize_key_text("Inhaltsübersicht") == "inhaltsuebersicht"
    result = Skip.detect_table_of_contents_skip(
        readable_spine_index=0, readable_spine_count=12,
        href="frontmatter.xhtml", title="Inhaltsübersicht",
        html=_toc_html("Inhaltsübersicht"),
    )
    assert result.should_skip


def test_foreword_with_normal_prose_never_discarded_as_toc():
    result = Skip.detect_table_of_contents_skip(
        readable_spine_index=2, readable_spine_count=12,
        href="vorwort.xhtml", title="Vorwort von Dev Ittycheria",
        html="<h1>Vorwort von Dev Ittycheria</h1><p>Ein Buch über Vertrieb und Führung.</p>",
    )
    assert not result.should_skip
    assert not Skip.is_likely_empty_non_reading_section("vorwort.xhtml", "Vorwort")


def test_german_imprint_identified_in_front_matter():
    assert Skip.has_publication_metadata_href_or_title_signal("impressum.xhtml", "Impressum")
    result = Skip.detect_publication_metadata_skip(
        readable_spine_index=0, readable_spine_count=12,
        href="impressum.xhtml", title="Impressum",
        html="<p>Alle Rechte vorbehalten.</p><p>Umschlaggestaltung: Der Verlag</p>",
    )
    assert result.should_skip


def test_de_word_in_normal_heading_not_a_toc():
    assert not Skip.has_table_of_contents_href_or_title_signal("kapitel-3.xhtml", "Inhaltliche Grundlagen")
    result = Skip.detect_table_of_contents_skip(
        readable_spine_index=2, readable_spine_count=12,
        href="kapitel-3.xhtml", title="Inhaltliche Grundlagen",
        html=_toc_html("Inhaltliche Grundlagen"),
    )
    # We deliberately do not skip merely because a genuine chapter has links.
    assert not result.should_skip


def test_web_catalogue_first_spoken_text_is_foreword():
    chapters = [
        SimpleNamespace(title="Inhaltsübersicht", text="Kapitel 1. Kapitel 2."),
        SimpleNamespace(title="Vorwort von Dev Ittycheria", text="Dieses Buch erklärt komplexe Vertriebsentscheidungen."),
        SimpleNamespace(title="Kapitel 1", text="Eine Einleitung über Sales und Leadership."),
    ]
    result = _catalogue(SimpleNamespace(chapters=chapters, book_title="Fachbuch", warnings=[], significant_warnings=[]))
    assert result["chapters"][0]["is_toc"]
    assert result["suggested_start"] == 1
    assert result["chapters"][1]["preview"] == chapters[1].text
