from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
import importlib
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import unquote, urlsplit
from typing import Any, Protocol

from tts_audiobook_tool.app_types import BookSection, SegmentationStrategy
from tts_audiobook_tool.app_support import app_text
from tts_audiobook_tool.constants import COL_DEFAULT, COL_DIM, PROJECT_TEXT_EPUB_FILE_NAME
from tts_audiobook_tool.text_util import strip_ansi_codes
from tts_audiobook_tool.text_ops.epub_section_skip_detector import EpubSectionSkipDetector
from tts_audiobook_tool.l import L
from tts_audiobook_tool.app_types.phrase import PhraseGroup, Reason
from tts_audiobook_tool.text_ops.phrase_grouper import PhraseGrouper
from tts_audiobook_tool.text_ops.heading_end_detector import recompute_heading_ends
from tts_audiobook_tool.text_ops import epub_css
from tts_audiobook_tool.text_ops.epub_css import EpubCssResolver, EpubStylesheets
from tts_audiobook_tool.text_ops.format_attach import attach_formatting_to_groups
from tts_audiobook_tool.text_ops.text_format import StyledText


@dataclass(frozen=True)
class EpubNavigationTarget:
    title: str
    path: str
    fragment: str
    href: str
    order: int
    depth: int = 0


@dataclass
class EpubSourceChapter:
    title: str
    href: str
    media_type: str
    html: str
    navigation_targets: list[EpubNavigationTarget] = field(default_factory=list)
    # The document's stylesheets, for display formatting; None when it has none
    css: EpubCssResolver | None = None
    # Keep positional constructor compatibility for existing Project-V5 code.
    # EPUB nav resources can also contain prose outside their <nav> elements.
    contains_reading_text_outside_nav: bool = False
    include_navigation_text: bool = False
    source_id: str = ""
    spine_index: int = -1


@dataclass
class EpubTextChapter:
    title: str
    href: str
    text: str
    # Source styles for display formatting; None when the chapter has none.
    # Its words match `text`; whitespace may differ (see text_format.align_masks).
    styled: StyledText | None = None
    id: str = ""


@dataclass(frozen=True)
class EpubSkippedSection:
    id: str
    title: str
    href: str
    reason: str
    preview: str
    category: str
    spine_index: int
    recoverable: bool = True
    included: bool = False


@dataclass(frozen=True)
class EpubContentReviewItem:
    id: str
    kind: str
    title: str
    href: str
    reason: str
    preview: str
    count: int = 1


@dataclass(frozen=True)
class EpubChapterCoverage:
    chapter_id: str
    title: str
    href: str
    source_start: int
    source_end: int
    phrase_group_start: int
    phrase_group_end: int
    source_characters: int
    spoken_characters: int
    complete: bool


@dataclass
class EpubSourceChapterLoad:
    chapters: list[EpubSourceChapter]
    book_title: str
    warnings: list[str]
    significant_warnings: list[str]
    skipped_sections: list[EpubSkippedSection] = field(default_factory=list)

    def _legacy_values(self) -> tuple[list[EpubSourceChapter], str, list[str], list[str]]:
        return self.chapters, self.book_title, self.warnings, self.significant_warnings

    def __iter__(self):
        # Preserve the historical four-value unpacking contract.
        yield from self._legacy_values()

    def __len__(self) -> int:
        return 4

    def __getitem__(self, index):
        # Keep tuple-style indexing/slicing compatible for downstream callers.
        return self._legacy_values()[index]


@dataclass
class EpubTextSlice:
    text: str
    navigation_target: EpubNavigationTarget | None = None
    styled: StyledText | None = None


@dataclass
class EpubTextExtractionResult:
    text: str
    warnings: list[str] = field(default_factory=list)
    significant_warnings: list[str] = field(default_factory=list)
    slices: list[EpubTextSlice] = field(default_factory=list)
    has_unresolved_navigation_target: bool = False
    styled: StyledText | None = None
    review_items: list[EpubContentReviewItem] = field(default_factory=list)


@dataclass
class EpubTextExtractionStats:
    inline_whitespace_repairs: int = 0


@dataclass
class EpubImportResult:
    phrase_groups: list[PhraseGroup]
    raw_text: str
    section_start_indices: list[int]
    chapters: list[EpubTextChapter]
    book_title: str = ""
    warnings: list[str] = field(default_factory=list)
    significant_warnings: list[str] = field(default_factory=list)
    skipped_sections: list[EpubSkippedSection] = field(default_factory=list)
    review_items: list[EpubContentReviewItem] = field(default_factory=list)
    coverage: list[EpubChapterCoverage] = field(default_factory=list)


@dataclass
class EpubChapterExtraction:
    chapters: list[EpubTextChapter]
    book_title: str = ""
    warnings: list[str] = field(default_factory=list)
    significant_warnings: list[str] = field(default_factory=list)
    skipped_sections: list[EpubSkippedSection] = field(default_factory=list)
    review_items: list[EpubContentReviewItem] = field(default_factory=list)


class EpubChapterTextExtractor(Protocol):
    def extract_text(self, chapter: EpubSourceChapter) -> EpubTextExtractionResult:
        ...


class BeautifulSoupEpubChapterTextExtractor:
    SKIP_TAGS = {
        "audio",
        "button",
        "canvas",
        "form",
        "head",
        "iframe",
        "img",
        "input",
        "noscript",
        "object",
        "script",
        "select",
        "style",
        "svg",
        "textarea",
        "title",
        "video",
    }
    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
    INLINE_WHITESPACE_REPAIR_WARNING_PREFIX = "EPUB text extraction repaired inline spacing"
    INLINE_WHITESPACE_REPAIR_WARNING_THRESHOLD = 10
    INLINE_TAGS = {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "cite",
        "code",
        "data",
        "dfn",
        "em",
        "i",
        "kbd",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    }

    def extract_text(self, chapter: EpubSourceChapter) -> EpubTextExtractionResult:
        BeautifulSoup = self.import_beautiful_soup()
        soup = BeautifulSoup(chapter.html, "html.parser")
        if not chapter.include_navigation_text:
            for nav in soup.find_all("nav"):
                nav.decompose()
        stats = EpubTextExtractionStats()
        warnings: list[str] = []
        significant_warnings: list[str] = []
        target_fragments = {
            target.fragment
            for target in chapter.navigation_targets
            if target.fragment
        }
        anchor_piece_indices: dict[str, int] = {}

        root = soup.body or soup
        pieces: list[str] = []
        # Style mask of each styled text piece, keyed by its index in `pieces`
        piece_styles: dict[int, int] = {}
        self.append_node_text(
            root,
            pieces,
            stats,
            target_fragments=target_fragments,
            anchor_piece_indices=anchor_piece_indices,
            piece_styles=piece_styles,
            css=chapter.css,
        )
        text = self.normalize_output_text("".join(pieces))
        slices, navigation_warnings, has_unresolved_navigation_target = self.make_text_slices(
            chapter,
            pieces,
            anchor_piece_indices,
            piece_styles,
        )
        warnings.extend(navigation_warnings)
        significant_warnings.extend(navigation_warnings)

        if stats.inline_whitespace_repairs >= self.INLINE_WHITESPACE_REPAIR_WARNING_THRESHOLD:
            warning = (
                f"{self.INLINE_WHITESPACE_REPAIR_WARNING_PREFIX} {stats.inline_whitespace_repairs} times "
                f"in {chapter.href}. Some inline spacing in this EPUB needed cleanup during import; "
                "review imported text if spacing looks unusual."
            )
            warnings.append(warning)
            significant_warnings.append(warning)

        if not text:
            warning = EpubExtractor.format_warning("No readable body text found in", chapter.href)
            warnings.append(warning)
            if not self.is_likely_non_reading_chapter(chapter):
                significant_warnings.append(warning)

        return EpubTextExtractionResult(
            text=text,
            warnings=warnings,
            significant_warnings=significant_warnings,
            slices=slices,
            has_unresolved_navigation_target=has_unresolved_navigation_target,
            styled=self.make_styled_text(pieces, piece_styles) if piece_styles else None,
            review_items=self.make_content_review_items(soup, chapter),
        )

    @classmethod
    def make_content_review_items(
            cls,
            soup: Any,
            chapter: EpubSourceChapter,
    ) -> list[EpubContentReviewItem]:
        """Describe complex source structures without inventing replacement speech."""
        items: list[EpubContentReviewItem] = []
        source_id = chapter.source_id or EpubExtractor.make_stable_id("source", chapter.href)

        tables = soup.find_all("table")
        if tables:
            first = tables[0]
            items.append(EpubContentReviewItem(
                id=EpubExtractor.make_stable_id(source_id, "table"),
                kind="table",
                title="Tabelle",
                href=chapter.href,
                reason=(
                    "Tabellenzellen werden in Quellreihenfolge zeilenweise ausgegeben. "
                    "Spaltenbezüge und Überschriften vor der Vertonung prüfen."
                ),
                preview=EpubExtractor.ellipsize_text(first.get_text(" ", strip=True), 220),
                count=len(tables),
            ))

        formulas = soup.find_all("math")
        if formulas:
            items.append(EpubContentReviewItem(
                id=EpubExtractor.make_stable_id(source_id, "formula"),
                kind="formula",
                title="Mathematische Formel",
                href=chapter.href,
                reason=(
                    "MathML wird nicht mathematisch interpretiert. Die extrahierten "
                    "Zeichen bleiben sichtbar; eine alternative Sprecherfassung braucht "
                    "eine menschliche Prüfung und Freigabe."
                ),
                preview=EpubExtractor.ellipsize_text(formulas[0].get_text(" ", strip=True), 220),
                count=len(formulas),
            ))

        figures = soup.find_all("figure")
        images = soup.find_all("img")
        if figures or images:
            first_figure = figures[0] if figures else None
            first_image = first_figure.find("img") if first_figure else (images[0] if images else None)
            caption = first_figure.find("figcaption") if first_figure else None
            preview = (
                caption.get_text(" ", strip=True) if caption
                else (first_image.get("alt", "") if first_image else "")
            )
            items.append(EpubContentReviewItem(
                id=EpubExtractor.make_stable_id(source_id, "figure"),
                kind="figure",
                title="Abbildung",
                href=chapter.href,
                reason=(
                    "Bilder werden nicht visuell ausgewertet. Vorhandene Bildunterschriften "
                    "bleiben Lesetext; Alt-Texte werden nicht ungefragt in den Sprechtext übernommen."
                ),
                preview=EpubExtractor.ellipsize_text(str(preview), 220),
                count=max(len(figures), len(images)),
            ))

        footnotes = [
            tag for tag in soup.find_all(True)
            if (
                "footnote" in str(tag.get("epub:type", "")).lower()
                or "doc-footnote" in str(tag.get("role", "")).lower()
                or any("footnote" in str(value).lower() for value in (tag.get("class") or []))
            )
        ]
        if footnotes:
            items.append(EpubContentReviewItem(
                id=EpubExtractor.make_stable_id(source_id, "footnote"),
                kind="footnote",
                title="Fußnote",
                href=chapter.href,
                reason=(
                    "Fußnoten werden in ihrer XHTML-Lesereihenfolge übernommen. "
                    "Position und Bezug zur Fußnotenmarke vor der Vertonung prüfen."
                ),
                preview=EpubExtractor.ellipsize_text(footnotes[0].get_text(" ", strip=True), 220),
                count=len(footnotes),
            ))

        return items

    @classmethod
    def is_likely_non_reading_chapter(cls, chapter: EpubSourceChapter) -> bool:
        if EpubSectionSkipDetector.is_likely_empty_non_reading_section(chapter.href, chapter.title):
            return True
        return cls.is_image_only_chapter(chapter.html)

    @classmethod
    def is_image_only_chapter(cls, html: str) -> bool:
        BeautifulSoup = cls.import_beautiful_soup()
        soup = BeautifulSoup(html, "html.parser")
        root = soup.body or soup
        if not root.find("img"):
            return False

        for tag in root.find_all(list(cls.SKIP_TAGS)):
            tag.decompose()

        text = cls.normalize_output_text(root.get_text(" "))
        return not text

    @staticmethod
    def is_comment_node(node: Any) -> bool:
        """
        Whether node is an HTML comment (`<!-- ... -->`).

        BeautifulSoup parses comments as NavigableString subclasses with `name` None, so without an explicit
        check they would be emitted as readable text. Comments are never rendered by readers; in practice they
        hold things like publisher-tooling leftovers, e.g. a commented-out duplicate `<img>` for Kindle.
        """
        try:
            Comment = getattr(importlib.import_module("bs4"), "Comment")
        except Exception as e:
            raise ImportError("Missing dependency beautifulsoup4. Reinstall requirements for EPUB import support.") from e
        return isinstance(node, Comment)

    @staticmethod
    def import_beautiful_soup() -> Any:
        try:
            module = importlib.import_module("bs4")
            return getattr(module, "BeautifulSoup")
        except Exception as e:
            raise ImportError("Missing dependency beautifulsoup4. Reinstall requirements for EPUB import support.") from e

    def node_to_text(self, node: Any, stats: EpubTextExtractionStats | None = None) -> str:
        stats = stats or EpubTextExtractionStats()
        pieces: list[str] = []
        self.append_node_text(node, pieces, stats)
        return "".join(pieces)

    def append_node_text(
            self,
            node: Any,
            pieces: list[str],
            stats: EpubTextExtractionStats,
            target_fragments: set[str] | None = None,
            anchor_piece_indices: dict[str, int] | None = None,
            piece_styles: dict[int, int] | None = None,
            style: int = 0,
            css: EpubCssResolver | None = None,
            suppress_block_boundaries: bool = False,
    ) -> None:
        """
        `piece_styles`, when given, receives the display-style mask of each
        styled text piece by its index in `pieces`; `style` is the mask
        inherited from ancestor elements, and `css` resolves the document's
        stylesheet rules (see docs-dev/text-formatting-layer.md).
        """
        name = getattr(node, "name", None)

        if target_fragments and anchor_piece_indices is not None and hasattr(node, "get"):
            anchor_values = [node.get("id")]
            if name == "a":
                anchor_values.append(node.get("name"))
            for anchor_value in anchor_values:
                if anchor_value in target_fragments and anchor_value not in anchor_piece_indices:
                    anchor_piece_indices[anchor_value] = len(pieces)

        if name in self.SKIP_TAGS:
            return
        if name == "br":
            pieces.append("\n")
            return
        if name == "hr":
            self.ensure_block_boundary(pieces)
            pieces.append("* * *\n\n")
            return
        if name == "tr":
            self.ensure_block_boundary(pieces)
            cells = node.find_all(["th", "td"], recursive=False)
            for index, cell in enumerate(cells):
                if index:
                    pieces.append("; ")
                self.append_node_text(
                    cell,
                    pieces,
                    stats,
                    target_fragments=target_fragments,
                    anchor_piece_indices=anchor_piece_indices,
                    piece_styles=piece_styles,
                    style=style,
                    css=css,
                    suppress_block_boundaries=True,
                )
            self.ensure_block_boundary(pieces)
            return
        is_block = name in self.BLOCK_TAGS
        if is_block and not suppress_block_boundaries:
            self.ensure_block_boundary(pieces)
        if name == "li":
            pieces.append(self.list_item_prefix(node))
        content_start = len(pieces)
        if piece_styles is not None:
            style = self.get_node_style(node, style, css)

        if not hasattr(node, "children"):
            piece_count = len(pieces)
            self.append_text_node(str(node), pieces)
            self.record_piece_style(pieces, piece_styles, piece_count, style)
        else:
            for child in node.children:
                if self.is_comment_node(child):
                    continue
                child_name = getattr(child, "name", None)
                if child_name is None:
                    piece_count = len(pieces)
                    self.append_text_node(str(child), pieces, name, child, stats)
                    self.record_piece_style(pieces, piece_styles, piece_count, style)
                else:
                    self.append_node_text(
                        child,
                        pieces,
                        stats,
                        target_fragments=target_fragments,
                        anchor_piece_indices=anchor_piece_indices,
                        piece_styles=piece_styles,
                        style=style,
                        css=css,
                        suppress_block_boundaries=suppress_block_boundaries,
                    )

        if is_block and not suppress_block_boundaries:
            # Empty blocks can be intentional spacers or image-only ornaments.
            # Keep that signal explicit, rather than inferring it from the sum
            # of paragraph boundaries contributed by nested layout wrappers.
            is_spacer = not any(piece.strip() for piece in pieces[content_start:])
            self.ensure_block_boundary(pieces, line_breaks=3 if is_spacer else 2)

    @staticmethod
    def list_item_prefix(node: Any) -> str:
        parent = getattr(node, "parent", None)
        if getattr(parent, "name", None) != "ol":
            return "- "
        siblings = parent.find_all("li", recursive=False)
        step = -1 if parent.has_attr("reversed") else 1
        try:
            number = int(parent.get("start")) if parent.get("start") is not None else (
                len(siblings) if step < 0 else 1
            )
        except (TypeError, ValueError):
            number = len(siblings) if step < 0 else 1
        for sibling in siblings:
            value = sibling.get("value")
            if value is not None:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    pass
            if sibling is node:
                return f"{number}. "
            number += step
        return f"{number}. "

    @staticmethod
    def get_node_style(node: Any, inherited_style: int, css: EpubCssResolver | None = None) -> int:
        """
        Display-style mask of an element, given the mask of its ancestors:
        tag defaults, then stylesheet rules, then its inline `style` attribute.
        """
        tag = (getattr(node, "name", None) or "").lower()
        declarations: dict[str, str] = {}
        if hasattr(node, "get"):
            if css is not None:
                raw_classes = node.get("class") or []
                classes = frozenset(raw_classes.split() if isinstance(raw_classes, str) else raw_classes)
                element_id = node.get("id")
                declarations.update(css.declarations_for(
                    tag, classes, element_id if isinstance(element_id, str) else None
                ))
            inline_css = node.get("style")
            if isinstance(inline_css, str) and inline_css:
                declarations.update(epub_css.parse_declarations(inline_css))
        return epub_css.resolve_style(tag, inherited_style, declarations)

    @staticmethod
    def record_piece_style(
            pieces: list[str],
            piece_styles: dict[int, int] | None,
            previous_piece_count: int,
            style: int,
    ) -> None:
        if piece_styles is None or not style:
            return
        for index in range(previous_piece_count, len(pieces)):
            piece_styles[index] = style

    @staticmethod
    def make_styled_text(
            pieces: list[str],
            piece_styles: dict[int, int],
            start: int = 0,
            end: int | None = None,
    ) -> StyledText:
        """
        Styled source text for pieces[start:end]. Not whitespace-normalized:
        its words match `normalize_output_text` of the same pieces, which is all
        `text_format.align_masks` requires.
        """
        texts: list[str] = []
        masks: list[int] = []
        for index in range(start, len(pieces) if end is None else end):
            text = unescape(pieces[index]).replace("\xa0", " ")
            texts.append(text)
            masks.extend([piece_styles.get(index, 0)] * len(text))
        return StyledText("".join(texts), masks)

    @staticmethod
    def ensure_block_boundary(pieces: list[str], line_breaks: int = 2) -> None:
        """Request paragraph spacing without multiplying it through nesting.

        Append only: navigation anchors refer to indices in `pieces`, so existing
        entries must not be merged or rewritten. Explicit br/spacer runs survive.
        """
        trailing_breaks = 0
        for piece in reversed(pieces):
            for char in reversed(piece):
                if not char.isspace():
                    if trailing_breaks < line_breaks:
                        pieces.append("\n" * (line_breaks - trailing_breaks))
                    return
                trailing_breaks += char == "\n"
            if trailing_breaks >= line_breaks:
                return
        if trailing_breaks < line_breaks:
            pieces.append("\n" * (line_breaks - trailing_breaks))

    @classmethod
    def make_text_slices(
            cls,
            chapter: EpubSourceChapter,
            pieces: list[str],
            anchor_piece_indices: dict[str, int],
            piece_styles: dict[int, int] | None = None,
    ) -> tuple[list[EpubTextSlice], list[str], bool]:
        warnings: list[str] = []

        def make_styled(start: int, end: int | None) -> StyledText | None:
            if not piece_styles:
                return None
            return cls.make_styled_text(pieces, piece_styles, start, end)

        has_unresolved_navigation_target = False
        targets_by_piece_index: dict[int, EpubNavigationTarget] = {}

        for target in chapter.navigation_targets:
            if target.fragment:
                piece_index = anchor_piece_indices.get(target.fragment)
                if piece_index is None:
                    warnings.append(
                        EpubExtractor.format_warning(
                            "EPUB navigation target not found in",
                            f"{chapter.href}: {target.href}",
                        )
                    )
                    has_unresolved_navigation_target = True
                    continue
            else:
                piece_index = 0

            previous = targets_by_piece_index.get(piece_index)
            if (
                previous is None
                or target.depth > previous.depth
                or (target.depth == previous.depth and target.order < previous.order)
            ):
                targets_by_piece_index[piece_index] = target

        targets_in_nav_order = sorted(
            targets_by_piece_index.items(),
            key=lambda item: item[1].order,
        )
        accepted_boundaries: list[tuple[int, EpubNavigationTarget]] = []
        last_piece_index = -1
        for piece_index, target in targets_in_nav_order:
            if piece_index < last_piece_index:
                warnings.append(
                    EpubExtractor.format_warning(
                        "EPUB navigation target is out of reading order in",
                        f"{chapter.href}: {target.href}",
                    )
                )
                continue
            accepted_boundaries.append((piece_index, target))
            last_piece_index = piece_index

        accepted_boundaries.sort(key=lambda item: item[0])
        slices: list[EpubTextSlice] = []
        start_piece_index = 0
        current_target: EpubNavigationTarget | None = None
        for piece_index, target in accepted_boundaries:
            raw_text = "".join(pieces[start_piece_index:piece_index])
            normalized_text = cls.normalize_output_text(raw_text)
            if normalized_text or current_target is not None:
                slices.append(EpubTextSlice(
                    normalized_text, current_target, make_styled(start_piece_index, piece_index)
                ))
            current_target = target
            start_piece_index = piece_index

        raw_text = "".join(pieces[start_piece_index:])
        normalized_text = cls.normalize_output_text(raw_text)
        if normalized_text or current_target is not None or not slices:
            slices.append(EpubTextSlice(normalized_text, current_target, make_styled(start_piece_index, None)))
        return slices, warnings, has_unresolved_navigation_target

    @classmethod
    def append_text_node(
            cls,
            text: str,
            pieces: list[str],
            parent_name: str | None = None,
            text_node: Any | None = None,
            stats: EpubTextExtractionStats | None = None,
    ) -> None:
        if not text.strip() and "\n" in text:
            if cls.should_repair_inline_whitespace_separator(parent_name, pieces, text_node):
                # Some EPUB conversion pipelines produce very ugly but technically recoverable markup where
                # normal inline word/sentence separators are represented as whitespace-only inline elements,
                # for example:
                #
                #   <span>Will it happen?</span><span>\n</span><span>That is the idea.</span>
                #
                # Treating all such newline-only text as structural indentation concatenates neighboring
                # sentences (`happen?That`). Treating all HTML newlines as paragraph breaks would be worse,
                # because most EPUB XHTML is pretty-printed with irrelevant indentation between block tags.
                # This branch is intentionally narrow: only whitespace-only text inside known inline tags,
                # with readable text already emitted and readable sibling content still ahead, is collapsed to
                # one ordinary space. The counter feeds a thresholded user-facing warning so highly affected
                # EPUBs remain visible instead of being silently "fixed".
                if pieces and not pieces[-1].endswith((" ", "\n")):
                    pieces.append(" ")
                if stats is not None:
                    stats.inline_whitespace_repairs += 1
            return
        pieces.append(text)

    @classmethod
    def should_repair_inline_whitespace_separator(
            cls,
            parent_name: str | None,
            pieces: list[str],
            text_node: Any | None,
    ) -> bool:
        if parent_name not in cls.INLINE_TAGS:
            return False
        if not pieces or not "".join(pieces).strip():
            return False
        return cls.has_readable_following_sibling(text_node)

    @classmethod
    def has_readable_following_sibling(cls, text_node: Any | None) -> bool:
        sibling = cls.get_next_sibling_after_inline_whitespace_node(text_node)
        while sibling is not None:
            name = getattr(sibling, "name", None)
            if name in cls.SKIP_TAGS:
                sibling = getattr(sibling, "next_sibling", None)
                continue
            if cls.is_comment_node(sibling):
                sibling = getattr(sibling, "next_sibling", None)
                continue
            if name is None:
                if str(sibling).strip():
                    return True
            elif cls.normalize_inline_text(sibling.get_text(" ")):
                return True
            sibling = getattr(sibling, "next_sibling", None)
        return False

    @classmethod
    def get_next_sibling_after_inline_whitespace_node(cls, text_node: Any | None) -> Any | None:
        sibling = getattr(text_node, "next_sibling", None)
        if sibling is not None:
            return sibling

        parent = getattr(text_node, "parent", None)
        while parent is not None and getattr(parent, "name", None) in cls.INLINE_TAGS:
            sibling = getattr(parent, "next_sibling", None)
            if sibling is not None:
                return sibling
            parent = getattr(parent, "parent", None)
        return None

    @staticmethod
    def normalize_inline_text(text: str) -> str:
        text = unescape(text).replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def normalize_output_text(text: str) -> str:
        text = unescape(text).replace("\xa0", " ")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = []
        for line in text.split("\n"):
            line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(line)
        text = "\n".join(lines)
        # Ordinary block boundaries are coalesced during traversal; three or
        # more line breaks now represent explicit spacing, not wrapper depth.
        text = re.sub(r"\n{3,}", "\n\n\n", text)
        return text.strip()


class EpubExtractor:
    DEFAULT_EXTRACTOR = BeautifulSoupEpubChapterTextExtractor()

    @staticmethod
    def make_stable_id(*parts: object) -> str:
        payload = "\\0".join(str(part) for part in parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def normalize_coverage_text(text: str) -> str:
        # Segmentation may normalize line endings and whitespace, but it must
        # preserve every non-whitespace source character in the spoken text.
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def get_ebook_title(epub_path: str) -> str:
        _, book_title, _, _ = EpubExtractor.load_source_chapters(epub_path)
        return book_title

    @staticmethod
    def import_epub(
            epub_path: str,
            max_words: int,
            segmentation_strategy: SegmentationStrategy,
            language_code: str,
            dialog_segmentation: bool = False,
            extractor: EpubChapterTextExtractor | None = None,
            include_skipped_ids: set[str] | None = None,
    ) -> EpubImportResult:
        extraction = EpubExtractor.extract_import_chapters(
            epub_path, extractor, include_skipped_ids=include_skipped_ids
        )
        warnings = extraction.warnings
        significant_warnings = extraction.significant_warnings

        phrase_groups: list[PhraseGroup] = []
        markers: list[int] = []
        raw_text_parts: list[str] = []
        sections: list[BookSection] = []
        format_mismatch_titles: list[str] = []
        coverage: list[EpubChapterCoverage] = []
        source_offset = 0

        for chapter in extraction.chapters:
            chapter_text = chapter.text
            group_start = len(phrase_groups)
            chapter_phrase_groups = PhraseGrouper.text_to_groups(
                chapter_text,
                max_words=max_words,
                strategy=segmentation_strategy,
                pysbd_lang=language_code,
                dialog_segmentation=dialog_segmentation,
            )
            spoken_text = " ".join(group.text for group in chapter_phrase_groups)
            source_coverage_text = EpubExtractor.normalize_coverage_text(chapter_text)
            spoken_coverage_text = EpubExtractor.normalize_coverage_text(spoken_text)
            complete = source_coverage_text == spoken_coverage_text
            coverage.append(EpubChapterCoverage(
                chapter_id=chapter.id,
                title=chapter.title,
                href=chapter.href,
                source_start=source_offset,
                source_end=source_offset + len(chapter_text),
                phrase_group_start=group_start,
                phrase_group_end=group_start + len(chapter_phrase_groups),
                source_characters=len(chapter_text),
                spoken_characters=len(spoken_text),
                complete=complete,
            ))
            if not complete:
                warning = EpubExtractor.format_warning(
                    "EPUB text coverage mismatch:",
                    f"{chapter.title or chapter.href} (source and segmented text differ)",
                )
                warnings.append(warning)
                significant_warnings.append(warning)
            # Display formatting is attached after segmentation, which only
            # rearranges whitespace (see docs-dev/text-formatting-layer.md)
            if chapter.styled is not None and chapter.styled.has_styles:
                alignment = attach_formatting_to_groups(chapter_phrase_groups, chapter.styled)
                if not alignment.is_exact:
                    format_mismatch_titles.append(chapter.title or chapter.href)
            if phrase_groups:
                markers.append(len(phrase_groups))
            EpubExtractor.mark_last_phrase_as_section(chapter_phrase_groups)
            sections.append(
                BookSection(title=chapter.title, phrase_groups=chapter_phrase_groups)
            )
            phrase_groups.extend(chapter_phrase_groups)
            raw_text_parts.append(chapter_text)
            source_offset += len(chapter_text) + 2

        if format_mismatch_titles:
            # Diagnostic only: formatting is decorative and never blocks import
            L.w(f"EPUB formatting partially unmatched in sections: {format_mismatch_titles}")

        # The same text/title-only pass runs again after confirmed editor saves.
        recompute_heading_ends(sections, language_code)
        raw_text = "\n\n".join(raw_text_parts)

        if not phrase_groups:
            warning = "EPUB import produced no text segments."
            warnings.append(warning)
            significant_warnings.append(warning)
            L.w(warning)

        return EpubImportResult(
            phrase_groups=phrase_groups,
            raw_text=raw_text,
            section_start_indices=markers,
            chapters=extraction.chapters,
            book_title=extraction.book_title,
            warnings=warnings,
            significant_warnings=significant_warnings,
            skipped_sections=extraction.skipped_sections,
            review_items=extraction.review_items,
            coverage=coverage,
        )

    @staticmethod
    def extract_import_chapters(
            epub_path: str,
            extractor: EpubChapterTextExtractor | None = None,
            include_skipped_ids: set[str] | None = None,
    ) -> EpubChapterExtraction:
        """
        Loads, extracts and assembles the logical sections that EPUB import keeps,
        in order: one per project `BookSection` created by `import_epub`.
        Also used to re-apply formatting to an existing project.
        """
        if include_skipped_ids:
            load_result = EpubExtractor.load_source_chapters(
                epub_path, include_skipped_ids=include_skipped_ids
            )
        else:
            # Preserve compatibility with tests and downstream callers that
            # replace the historical one-argument loader.
            load_result = EpubExtractor.load_source_chapters(epub_path)
        source_chapters, book_title, warnings, significant_warnings = load_result
        skipped_sections = list(getattr(load_result, "skipped_sections", []))
        review_items: list[EpubContentReviewItem] = []
        EpubExtractor.log_warnings(warnings)
        EpubExtractor.log_warnings([warning for warning in significant_warnings if warning not in warnings])
        extractor = extractor or EpubExtractor.DEFAULT_EXTRACTOR

        extracted_chapters: list[tuple[EpubSourceChapter, EpubTextExtractionResult]] = []
        did_report_inline_whitespace_repair_warning = False
        for source_chapter in source_chapters:
            result = extractor.extract_text(source_chapter)
            result_warnings, result_significant_warnings, did_report_inline_whitespace_repair_warning = (
                EpubExtractor.filter_repeated_inline_whitespace_repair_warnings(
                    result.warnings,
                    result.significant_warnings,
                    did_report_inline_whitespace_repair_warning,
                )
            )
            EpubExtractor.log_warnings(result_warnings)
            warnings.extend(result_warnings)
            significant_warnings.extend(result_significant_warnings)
            review_items.extend(result.review_items)
            extracted_chapters.append((source_chapter, result))

        text_chapters = EpubExtractor.assemble_text_chapters(
            extracted_chapters,
            warnings,
            significant_warnings,
            skipped_sections=skipped_sections,
        )

        kept_chapters: list[EpubTextChapter] = []
        for chapter in text_chapters:
            chapter_text = chapter.text
            if not chapter_text.strip():
                continue
            if not app_text.is_vocalizable(chapter_text):
                # Ornament-only pages (a lone dinkus, divider, etc) contain no
                # speakable text. Keeping them would force a section whose
                # groups are all non-vocalizable, so skip them outright.
                warning = EpubExtractor.format_warning(
                    "EPUB section has no vocalizable text and was skipped:",
                    chapter.title or chapter.href,
                )
                warnings.append(warning)
                significant_warnings.append(warning)
                EpubExtractor.log_warnings([warning])
                restored_index = next(
                    (
                        index for index, item in enumerate(skipped_sections)
                        if item.included
                        and item.href.split("#", 1)[0] == chapter.href.split("#", 1)[0]
                    ),
                    None,
                )
                if restored_index is not None:
                    previous = skipped_sections[restored_index]
                    skipped_sections[restored_index] = EpubSkippedSection(
                        id=previous.id,
                        title=previous.title,
                        href=previous.href,
                        reason="Restored section contains no vocalizable text after extraction.",
                        preview=EpubExtractor.ellipsize_text(chapter.text, 240),
                        category="non-vocalizable",
                        spine_index=previous.spine_index,
                        recoverable=False,
                        included=False,
                    )
                else:
                    skipped_sections.append(EpubSkippedSection(
                        id=chapter.id or EpubExtractor.make_stable_id("chapter", chapter.href, chapter.title),
                        title=chapter.title,
                        href=chapter.href,
                        reason="Section contains no vocalizable text after extraction",
                        preview=EpubExtractor.ellipsize_text(chapter.text, 240),
                        category="non-vocalizable",
                        spine_index=-1,
                        recoverable=False,
                        included=False,
                    ))
                continue
            kept_chapters.append(chapter)

        skipped_sections.sort(
            key=lambda item: item.spine_index if item.spine_index >= 0 else 1_000_000
        )
        return EpubChapterExtraction(
            chapters=kept_chapters,
            book_title=book_title,
            warnings=warnings,
            significant_warnings=significant_warnings,
            skipped_sections=skipped_sections,
            review_items=review_items[:250],
        )

    @staticmethod
    def assemble_text_chapters(
            extracted_chapters: list[tuple[EpubSourceChapter, EpubTextExtractionResult]],
            warnings: list[str],
            significant_warnings: list[str],
            skipped_sections: list[EpubSkippedSection] | None = None,
    ) -> list[EpubTextChapter]:
        has_navigation_targets = any(
            source_chapter.navigation_targets
            for source_chapter, _ in extracted_chapters
        )
        has_unresolved_navigation_target = any(
            result.has_unresolved_navigation_target
            for _, result in extracted_chapters
        )
        if has_navigation_targets and not has_unresolved_navigation_target:
            omitted_navigation_targets: list[EpubNavigationTarget] = []
            text_chapters, used_navigation = EpubExtractor.assemble_navigation_text_chapters(
                extracted_chapters,
                warnings,
                significant_warnings,
                omitted_navigation_targets=omitted_navigation_targets,
            )
            if used_navigation:
                if skipped_sections is not None:
                    skipped_sections.extend(
                        EpubExtractor.make_empty_navigation_skip(target)
                        for target in omitted_navigation_targets
                    )
                return text_chapters

        if extracted_chapters:
            warning = "EPUB navigation is unavailable or unusable; section boundaries were derived from spine documents."
            warnings.append(warning)
            significant_warnings.append(warning)
            EpubExtractor.log_warnings([warning])
        return EpubExtractor.make_spine_text_chapters(extracted_chapters)

    @staticmethod
    def make_spine_text_chapters(
            extracted_chapters: list[tuple[EpubSourceChapter, EpubTextExtractionResult]],
    ) -> list[EpubTextChapter]:
        return [
            EpubTextChapter(
                title=source_chapter.title,
                href=source_chapter.href,
                text=result.text,
                styled=result.styled,
                id=EpubExtractor.make_stable_id(
                    "spine-section", source_chapter.href, source_chapter.title
                ),
            )
            for source_chapter, result in extracted_chapters
            if result.text.strip()
        ]

    @staticmethod
    def assemble_navigation_text_chapters(
            extracted_chapters: list[tuple[EpubSourceChapter, EpubTextExtractionResult]],
            warnings: list[str],
            significant_warnings: list[str],
            omitted_navigation_targets: list[EpubNavigationTarget] | None = None,
    ) -> tuple[list[EpubTextChapter], bool]:
        text_chapters: list[EpubTextChapter] = []
        text_parts: list[str] = []
        # Parallel to text_parts; None where a part has no styles
        styled_parts: list[StyledText | None] = []
        current_target: EpubNavigationTarget | None = None
        fallback_title = ""
        fallback_href = ""
        used_navigation = False

        def flush_text_chapter() -> None:
            nonlocal text_parts, styled_parts, current_target, fallback_title, fallback_href, used_navigation
            if not text_parts:
                return
            # Merged pseudo-sections keep a section-like boundary: two blank lines between parts.
            title = current_target.title if current_target is not None else fallback_title
            href = current_target.href if current_target is not None else fallback_href
            separator = "\n\n\n"
            styled = None
            if any(part is not None for part in styled_parts):
                styled = StyledText.join(
                    [
                        part if part is not None else StyledText.plain(text)
                        for text, part in zip(text_parts, styled_parts)
                    ],
                    separator,
                )
            text_chapters.append(EpubTextChapter(
                title=title,
                href=href,
                text=separator.join(text_parts),
                styled=styled,
                id=EpubExtractor.make_stable_id("navigation-section", href, title),
            ))
            used_navigation = used_navigation or current_target is not None
            text_parts = []
            styled_parts = []
            current_target = None
            fallback_title = ""
            fallback_href = ""

        for source_chapter, result in extracted_chapters:
            if result.slices:
                slices = result.slices
            else:
                document_targets = [
                    target
                    for target in source_chapter.navigation_targets
                    if not target.fragment
                ]
                document_target = max(document_targets, key=lambda target: target.order, default=None)
                slices = [EpubTextSlice(result.text, document_target, result.styled)]
            for text_slice in slices:
                if text_slice.navigation_target is not None:
                    if text_parts:
                        flush_text_chapter()
                    elif current_target is not None:
                        warning = EpubExtractor.format_warning(
                            "Omitted empty EPUB navigation section before",
                            f"{text_slice.navigation_target.href}: {current_target.title}",
                        )
                        warnings.append(warning)
                        significant_warnings.append(warning)
                        if omitted_navigation_targets is not None:
                            omitted_navigation_targets.append(current_target)
                    current_target = text_slice.navigation_target

                if text_slice.text.strip():
                    if current_target is None and not text_parts:
                        fallback_title = source_chapter.title
                        fallback_href = source_chapter.href
                    text_parts.append(text_slice.text)
                    styled = text_slice.styled
                    styled_parts.append(styled if styled is not None and styled.has_styles else None)

            if (
                not result.text.strip()
                and current_target is not None
                and current_target.path == EpubExtractor.normalize_toc_href(source_chapter.href)
                and EpubSectionSkipDetector.is_likely_empty_non_reading_section(
                    source_chapter.href,
                    current_target.title,
                )
            ):
                if omitted_navigation_targets is not None:
                    omitted_navigation_targets.append(current_target)
                current_target = None

        if text_parts:
            flush_text_chapter()
        elif current_target is not None:
            warning = f"Omitted empty EPUB navigation section: {current_target.title}"
            warnings.append(warning)
            significant_warnings.append(warning)
            if omitted_navigation_targets is not None:
                omitted_navigation_targets.append(current_target)

        return text_chapters, used_navigation

    @staticmethod
    def filter_repeated_inline_whitespace_repair_warnings(
            warnings: list[str],
            significant_warnings: list[str],
            did_report_warning: bool,
    ) -> tuple[list[str], list[str], bool]:
        filtered_warnings: list[str] = []
        filtered_significant_warnings: list[str] = []

        for warning in warnings:
            if EpubExtractor.is_inline_whitespace_repair_warning(warning):
                if did_report_warning:
                    continue
                did_report_warning = True
            filtered_warnings.append(warning)

        for warning in significant_warnings:
            if EpubExtractor.is_inline_whitespace_repair_warning(warning) and warning not in filtered_warnings:
                if did_report_warning:
                    continue
                did_report_warning = True
            filtered_significant_warnings.append(warning)

        return filtered_warnings, filtered_significant_warnings, did_report_warning

    @staticmethod
    def is_inline_whitespace_repair_warning(warning: str) -> bool:
        return warning.startswith(BeautifulSoupEpubChapterTextExtractor.INLINE_WHITESPACE_REPAIR_WARNING_PREFIX)

    @staticmethod
    def mark_last_phrase_as_section(phrase_groups: list[PhraseGroup]) -> None:
        if not phrase_groups:
            return
        last_group = phrase_groups[-1]
        if not last_group.phrases:
            return
        last_phrase = last_group.phrases[-1]
        last_phrase.reason = Reason.SECTION_BREAK

    @staticmethod
    def navigation_has_reading_prose(chapter: EpubSourceChapter) -> bool:
        """Keep short prose outside navigation, but not a TOC heading by itself.

        A vocalizable paragraph or blockquote outside <nav> is sufficient;
        no arbitrary minimum word count is applied. The canonical extractor
        remains the source of the actual imported text.
        """
        BeautifulSoup = BeautifulSoupEpubChapterTextExtractor.import_beautiful_soup()
        soup = BeautifulSoup(chapter.html, "html.parser")
        prose_blocks = (
            tag for tag in soup.find_all(("p", "blockquote"))
            if tag.find_parent("nav") is None
        )
        if not any(app_text.is_vocalizable(tag.get_text(" ", strip=True)) for tag in prose_blocks):
            return False
        result = BeautifulSoupEpubChapterTextExtractor().extract_text(chapter)
        return bool(result.text.strip()) and app_text.is_vocalizable(result.text)

    @staticmethod
    def load_source_chapters(
            epub_path: str,
            include_skipped_ids: set[str] | None = None,
    ) -> EpubSourceChapterLoad:
        include_skipped_ids = set(include_skipped_ids or ())
        if not os.path.exists(epub_path):
            return EpubSourceChapterLoad([], "", [], [f"EPUB file does not exist: {epub_path}"])
        if os.path.splitext(epub_path)[1].lower() != ".epub":
            return EpubSourceChapterLoad([], "", [], [f"File does not have .epub suffix: {epub_path}"])

        try:
            epub = importlib.import_module("ebooklib.epub")
        except Exception as e:
            raise ImportError("Missing dependency EbookLib. Reinstall requirements for EPUB import support.") from e

        try:
            book = epub.read_epub(epub_path, options={"ignore_ncx": True})
        except Exception as e:
            message = f"Error reading EPUB: {e}"
            L.e(message)
            return EpubSourceChapterLoad([], "", [], [message])

        book_title = EpubExtractor.extract_book_title(book)
        navigation_targets = EpubExtractor.extract_navigation_targets(book)
        navigation_targets_by_path: dict[str, list[EpubNavigationTarget]] = {}
        for navigation_target in navigation_targets:
            navigation_targets_by_path.setdefault(navigation_target.path, []).append(navigation_target)
        warnings: list[str] = []
        significant_warnings: list[str] = []
        skipped_sections: list[EpubSkippedSection] = []
        source_chapter_candidates: list[EpubSourceChapter] = []
        source_chapters: list[EpubSourceChapter] = []
        stylesheets = EpubExtractor.load_stylesheets(book)
        reading_spine_position = 0
        known_recoverable_ids: set[str] = set()

        for spine_index, spine_item in enumerate(book.spine):
            item_id = spine_item[0] if isinstance(spine_item, tuple) else spine_item
            item = book.get_item_with_id(item_id)
            if item is None:
                href = str(item_id)
                source_id = EpubExtractor.make_stable_id("spine-source", spine_index, item_id, href)
                warning = EpubExtractor.format_warning("EPUB spine item not found:", str(item_id))
                warnings.append(warning)
                significant_warnings.append(warning)
                skipped_sections.append(EpubSkippedSection(
                    id=source_id, title=str(item_id), href=href,
                    reason="Spine item is missing from the EPUB package.",
                    preview="", category="missing-resource", spine_index=spine_index,
                    recoverable=False, included=False,
                ))
                continue

            href = getattr(item, "file_name", "") or str(item_id)
            source_id = EpubExtractor.make_stable_id("spine-source", spine_index, item_id, href)
            media_type = getattr(item, "media_type", "")
            html = EpubExtractor.decode_item_content(item)
            if not html:
                warning = EpubExtractor.format_warning("EPUB spine item has no decodable content:", str(item_id))
                warnings.append(warning)
                significant_warnings.append(warning)
                skipped_sections.append(EpubSkippedSection(
                    id=source_id, title=str(item_id), href=href,
                    reason="Spine item content is empty or could not be decoded.",
                    preview="", category="unreadable-resource", spine_index=spine_index,
                    recoverable=False, included=False,
                ))
                continue

            item_navigation_targets = navigation_targets_by_path.get(EpubExtractor.normalize_toc_href(href), [])
            title = (
                item_navigation_targets[0].title
                if item_navigation_targets
                else EpubExtractor.extract_title(html, href)
            )
            is_navigation = EpubSectionSkipDetector.is_navigation_document(
                str(item_id), href, item)
            navigation_chapter = EpubSourceChapter(
                title=title, href=href, media_type=media_type, html=html,
                navigation_targets=list(item_navigation_targets),
                css=EpubExtractor.get_document_css(item, href, html, stylesheets),
                source_id=source_id,
                spine_index=spine_index,
            )
            preserve_nav_prose = (
                is_navigation
                and EpubExtractor.navigation_has_reading_prose(navigation_chapter)
            )
            if is_navigation and not preserve_nav_prose:
                toc_skip_decision = EpubSectionSkipDetector.detect_table_of_contents_skip(
                    readable_spine_index=reading_spine_position,
                    readable_spine_count=max(1, reading_spine_position + 1),
                    href=href,
                    title=title,
                    html=html,
                )
                if toc_skip_decision.should_skip:
                    included = navigation_chapter.source_id in include_skipped_ids
                    skipped_sections.append(EpubExtractor.make_skipped_section(
                        navigation_chapter,
                        toc_skip_decision.reason,
                        category="table-of-contents",
                        recoverable=True,
                        included=included,
                    ))
                    known_recoverable_ids.add(navigation_chapter.source_id)
                    if not included:
                        EpubExtractor.append_skipped_section_warning(
                            warnings, significant_warnings, navigation_chapter,
                            toc_skip_decision.reason,
                        )
                        continue
                    # Explicit restoration may include the navigation list itself.
                    navigation_chapter.include_navigation_text = True
                    source_chapters.append(navigation_chapter)
                    continue

                reason = (
                    "Navigation document contains no vocalizable prose outside <nav>. "
                    "Its link list remains metadata unless explicitly restored."
                )
                included = navigation_chapter.source_id in include_skipped_ids
                skipped_sections.append(EpubExtractor.make_skipped_section(
                    navigation_chapter,
                    reason,
                    category="navigation-document",
                    recoverable=True,
                    included=included,
                ))
                known_recoverable_ids.add(navigation_chapter.source_id)
                if not included:
                    warning = EpubExtractor.format_warning("Skipped EPUB navigation document:", href)
                    warnings.append(warning)
                    continue
                navigation_chapter.include_navigation_text = True
                source_chapters.append(navigation_chapter)
                continue

            if preserve_nav_prose:
                navigation_chapter.contains_reading_text_outside_nav = True
                warning = EpubExtractor.format_warning(
                    "EPUB navigation document contains readable prose outside <nav>; retained:",
                    href,
                )
                warnings.append(warning)
                significant_warnings.append(warning)
            source_chapter_candidates.append(navigation_chapter)
            if EpubSectionSkipDetector.is_text_bearing_spine_document(html):
                reading_spine_position += 1

        candidate_paths = {
            EpubExtractor.normalize_toc_href(source_chapter.href)
            for source_chapter in source_chapter_candidates
        }
        for navigation_target in navigation_targets:
            if navigation_target.path in candidate_paths:
                continue
            warning = EpubExtractor.format_warning(
                "EPUB navigation target does not map to a spine document:",
                navigation_target.href,
            )
            warnings.append(warning)
            significant_warnings.append(warning)

        EpubExtractor.remove_backward_document_navigation_targets(
            source_chapter_candidates,
            warnings,
            significant_warnings,
        )
        text_bearing_flags = [
            EpubSectionSkipDetector.is_text_bearing_spine_document(source_chapter.html)
            for source_chapter in source_chapter_candidates
        ]
        reading_spine_count = sum(text_bearing_flags)
        reading_spine_position = 0
        for source_chapter, is_text_bearing in zip(source_chapter_candidates, text_bearing_flags):
            publication_metadata_skip_decision = EpubSectionSkipDetector.detect_publication_metadata_skip(
                reading_spine_position,
                reading_spine_count,
                source_chapter.href,
                source_chapter.title,
                source_chapter.html,
            )
            if publication_metadata_skip_decision.should_skip:
                included = source_chapter.source_id in include_skipped_ids
                skipped_sections.append(EpubExtractor.make_skipped_section(
                    source_chapter,
                    publication_metadata_skip_decision.reason,
                    category="publication-metadata",
                    recoverable=True,
                    included=included,
                ))
                known_recoverable_ids.add(source_chapter.source_id)
                if not included:
                    EpubExtractor.append_skipped_section_warning(
                        warnings,
                        significant_warnings,
                        source_chapter,
                        publication_metadata_skip_decision.reason,
                    )
                    continue
                # Keep heuristic positions independent of the user's restore
                # choice so the skipped-section inventory remains stable.
                source_chapters.append(source_chapter)
                continue

            toc_skip_decision = EpubSectionSkipDetector.detect_table_of_contents_skip(
                reading_spine_position,
                reading_spine_count,
                source_chapter.href,
                source_chapter.title,
                source_chapter.html,
            )
            if toc_skip_decision.should_skip and not source_chapter.contains_reading_text_outside_nav:
                included = source_chapter.source_id in include_skipped_ids
                skipped_sections.append(EpubExtractor.make_skipped_section(
                    source_chapter,
                    toc_skip_decision.reason,
                    category="table-of-contents",
                    recoverable=True,
                    included=included,
                ))
                known_recoverable_ids.add(source_chapter.source_id)
                if not included:
                    EpubExtractor.append_skipped_section_warning(
                        warnings,
                        significant_warnings,
                        source_chapter,
                        toc_skip_decision.reason,
                    )
                    continue
                source_chapters.append(source_chapter)
                # Keep the heuristic scan position independent of whether a
                # user restores this TOC; otherwise later skip decisions vary.
                continue

            source_chapters.append(source_chapter)
            if is_text_bearing:
                reading_spine_position += 1

        unknown_ids = include_skipped_ids - known_recoverable_ids
        if unknown_ids:
            raise ValueError(
                "One or more selected EPUB sections are no longer available for restoration. "
                "Please re-import the EPUB and review the current skipped-section list."
            )

        if not source_chapters:
            warning = "No readable EPUB spine documents found."
            significant_warnings.append(warning)

        skipped_sections.sort(
            key=lambda item: item.spine_index if item.spine_index >= 0 else 1_000_000
        )
        return EpubSourceChapterLoad(
            chapters=source_chapters,
            book_title=book_title,
            warnings=warnings,
            significant_warnings=significant_warnings,
            skipped_sections=skipped_sections,
        )

    @staticmethod
    def load_stylesheets(book: Any) -> EpubStylesheets:
        """The book's CSS resources by package path, for display formatting."""
        css_by_path: dict[str, str] = {}
        try:
            items = list(getattr(book, "items", []) or [])
        except Exception:
            items = []
        for item in items:
            if getattr(item, "media_type", "") != "text/css":
                continue
            path = EpubExtractor.normalize_toc_href(getattr(item, "file_name", "") or "")
            content = getattr(item, "content", None)
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            if path and isinstance(content, str):
                css_by_path[path] = content
        return EpubStylesheets(css_by_path)

    @staticmethod
    def get_document_css(item: Any, href: str, html: str, stylesheets: EpubStylesheets) -> EpubCssResolver | None:
        """
        Stylesheet resolver for a spine document. Stylesheet links are read from the
        item's original bytes, because EbookLib's `get_content()` rebuilds `<head>`
        without them. Any failure only means no stylesheet formatting.
        """
        raw = getattr(item, "content", None)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            raw = html
        try:
            refs = epub_css.extract_stylesheet_refs(raw, EpubExtractor.normalize_toc_href(href))
            return stylesheets.resolver_for(refs)
        except Exception as e:
            L.w(f"Could not read stylesheets of {href}: {e}")
            return None

    @staticmethod
    def get_navigation_resource_base_path(book: Any) -> str:
        try:
            items = getattr(book, "items", [])
        except Exception:
            return ""

        for item in items:
            properties = getattr(item, "properties", [])
            if "nav" in properties or type(item).__name__ == "EpubNav":
                # EbookLib has already resolved EPUB 3 nav links relative to this resource.
                return ""

        for item in items:
            if getattr(item, "media_type", "") != "application/x-dtbncx+xml":
                continue
            file_name = EpubExtractor.normalize_toc_href(getattr(item, "file_name", ""))
            if file_name:
                directory = posixpath.dirname(file_name)
                return "" if directory == "." else directory
        return ""

    @staticmethod
    def extract_navigation_document_targets(book: Any) -> list[EpubNavigationTarget]:
        # EbookLib reads only a label element's direct .text, losing inline markup such as
        # <a><strong>Part One</strong></a>. Read EPUB 3 labels from the document instead.
        for item in getattr(book, "items", []):
            properties = getattr(item, "properties", []) or []
            if "nav" not in properties and type(item).__name__ != "EpubNav":
                continue
            # EbookLib's get_content() pretty-prints XHTML, adding whitespace between adjacent spans.
            # Read the original navigation bytes when available so labels retain source adjacency.
            raw_content = getattr(item, "content", None)
            if isinstance(raw_content, bytes):
                html = raw_content.decode("utf-8", errors="replace")
            elif isinstance(raw_content, str):
                html = raw_content
            else:
                html = EpubExtractor.decode_item_content(item)
            if not html:
                continue
            BeautifulSoup = BeautifulSoupEpubChapterTextExtractor.import_beautiful_soup()
            soup = BeautifulSoup(html, "html.parser")
            navigation_base_path = posixpath.dirname(getattr(item, "file_name", "") or "")
            for nav in soup.find_all("nav"):
                if "toc" not in str(nav.get("epub:type", "")).split():
                    continue
                nav_list = nav.find("ol")
                if nav_list is None:
                    continue
                navigation_targets: list[EpubNavigationTarget] = []
                for link in nav_list.find_all("a", href=True):
                    # Inline markup is not a word boundary; preserve adjacency and source whitespace.
                    title = BeautifulSoupEpubChapterTextExtractor.normalize_inline_text(link.get_text())
                    depth = 0
                    parent_list = link.find_parent("ol")
                    while parent_list is not None and parent_list is not nav_list:
                        depth += 1
                        parent_list = parent_list.find_parent("ol")
                    target = EpubExtractor.make_navigation_target(
                        link.get("href", ""),
                        title,
                        len(navigation_targets),
                        navigation_base_path,
                        depth,
                    )
                    if target is not None:
                        navigation_targets.append(target)
                if navigation_targets:
                    return navigation_targets
        return []

    @staticmethod
    def extract_navigation_targets(book: Any) -> list[EpubNavigationTarget]:
        document_targets = EpubExtractor.extract_navigation_document_targets(book)
        if document_targets:
            return document_targets

        try:
            toc = getattr(book, "toc", [])
        except Exception:
            return []

        navigation_targets: list[EpubNavigationTarget] = []
        navigation_base_path = EpubExtractor.get_navigation_resource_base_path(book)
        EpubExtractor.collect_navigation_targets(
            toc,
            navigation_targets,
            navigation_base_path,
        )
        return navigation_targets

    @staticmethod
    def collect_navigation_targets(
            toc_items: Any,
            navigation_targets: list[EpubNavigationTarget],
            navigation_base_path: str = "",
            depth: int = 0,
    ) -> None:
        if toc_items is None:
            return

        if isinstance(toc_items, tuple):
            for tuple_index, toc_item in enumerate(toc_items):
                EpubExtractor.collect_navigation_targets(
                    toc_item,
                    navigation_targets,
                    navigation_base_path,
                    depth + (1 if tuple_index > 0 else 0),
                )
            return

        if isinstance(toc_items, list):
            for toc_item in toc_items:
                EpubExtractor.collect_navigation_targets(
                    toc_item,
                    navigation_targets,
                    navigation_base_path,
                    depth,
                )
            return

        href = getattr(toc_items, "href", "")
        raw_title = getattr(toc_items, "title", "")
        title = BeautifulSoupEpubChapterTextExtractor.normalize_inline_text(raw_title) if isinstance(raw_title, str) else ""
        navigation_target = EpubExtractor.make_navigation_target(
            href,
            title,
            len(navigation_targets),
            navigation_base_path,
            depth,
        )
        if navigation_target is not None:
            navigation_targets.append(navigation_target)

        subitems = getattr(toc_items, "subitems", None)
        if subitems:
            EpubExtractor.collect_navigation_targets(
                subitems,
                navigation_targets,
                navigation_base_path,
                depth + 1,
            )

    @staticmethod
    def make_navigation_target(
            href: str,
            title: str,
            order: int,
            navigation_base_path: str = "",
            depth: int = 0,
    ) -> EpubNavigationTarget | None:
        if not isinstance(href, str) or not title:
            return None
        parts = urlsplit(href)
        if parts.scheme or parts.netloc:
            return None
        path_href = parts.path
        if navigation_base_path and not path_href.startswith("/"):
            path_href = posixpath.join(navigation_base_path, path_href)
        path = EpubExtractor.normalize_toc_href(path_href)
        if not path:
            return None
        fragment = unquote(parts.fragment).strip()
        canonical_href = path + (f"#{fragment}" if fragment else "")
        return EpubNavigationTarget(
            title=title,
            path=path,
            fragment=fragment,
            href=canonical_href,
            order=order,
            depth=depth,
        )

    @staticmethod
    def extract_toc_title_by_href(book: Any) -> dict[str, str]:
        title_by_href: dict[str, str] = {}
        for target in EpubExtractor.extract_navigation_targets(book):
            title_by_href.setdefault(target.path, target.title)
        return title_by_href

    @staticmethod
    def collect_toc_titles(toc_items: Any, title_by_href: dict[str, str]) -> None:
        navigation_targets: list[EpubNavigationTarget] = []
        EpubExtractor.collect_navigation_targets(toc_items, navigation_targets)
        for target in navigation_targets:
            title_by_href.setdefault(target.path, target.title)

    @staticmethod
    def normalize_toc_href(href: str) -> str:
        if not isinstance(href, str):
            return ""
        parts = urlsplit(href)
        if parts.scheme or parts.netloc:
            return ""
        path = unquote(parts.path).replace("\\", "/").strip()
        if not path:
            return ""
        path = posixpath.normpath(path)
        while path.startswith("./"):
            path = path[2:]
        return path.lstrip("/")

    @staticmethod
    def remove_backward_document_navigation_targets(
            source_chapters: list[EpubSourceChapter],
            warnings: list[str],
            significant_warnings: list[str],
    ) -> None:
        spine_index_by_path = {
            EpubExtractor.normalize_toc_href(source_chapter.href): index
            for index, source_chapter in enumerate(source_chapters)
        }
        all_targets = sorted(
            (
                target
                for source_chapter in source_chapters
                for target in source_chapter.navigation_targets
            ),
            key=lambda target: target.order,
        )
        rejected_orders: set[int] = set()
        last_spine_index = -1
        for target in all_targets:
            spine_index = spine_index_by_path.get(target.path)
            if spine_index is None:
                continue
            if spine_index < last_spine_index:
                warning = EpubExtractor.format_warning("EPUB navigation target is out of spine order:", target.href)
                warnings.append(warning)
                significant_warnings.append(warning)
                rejected_orders.add(target.order)
                continue
            last_spine_index = spine_index

        if not rejected_orders:
            return
        for source_chapter in source_chapters:
            source_chapter.navigation_targets = [
                target
                for target in source_chapter.navigation_targets
                if target.order not in rejected_orders
            ]

    @staticmethod
    def extract_book_title(book: Any) -> str:
        try:
            metadata_values = book.get_metadata("DC", "title")
        except Exception:
            return ""

        for metadata_value in metadata_values:
            value = metadata_value[0] if isinstance(metadata_value, tuple) and metadata_value else metadata_value
            if not isinstance(value, str):
                continue
            title = BeautifulSoupEpubChapterTextExtractor.normalize_inline_text(value)
            if title:
                return title
        return ""

    @staticmethod
    def make_empty_navigation_skip(target: EpubNavigationTarget) -> EpubSkippedSection:
        return EpubSkippedSection(
            id=EpubExtractor.make_stable_id("empty-navigation-section", target.href, target.title),
            title=target.title,
            href=target.href,
            reason="Navigation anchor has no readable text in the retained spine content.",
            preview="",
            category="empty-navigation-section",
            spine_index=-1,
            recoverable=False,
            included=False,
        )

    @staticmethod
    def make_skipped_section(
            source_chapter: EpubSourceChapter,
            reason: str,
            category: str,
            recoverable: bool,
            included: bool = False,
    ) -> EpubSkippedSection:
        preview = EpubSectionSkipDetector.html_to_text_preview(source_chapter.html)
        return EpubSkippedSection(
            id=source_chapter.source_id or EpubExtractor.make_stable_id(
                "spine-source", source_chapter.spine_index, source_chapter.href
            ),
            title=source_chapter.title or source_chapter.href,
            href=source_chapter.href,
            reason=reason,
            preview=EpubExtractor.ellipsize_text(preview, 240),
            category=category,
            spine_index=source_chapter.spine_index,
            recoverable=recoverable,
            included=included,
        )

    @staticmethod
    def append_skipped_section_warning(
            warnings: list[str],
            significant_warnings: list[str],
            source_chapter: EpubSourceChapter,
            reason: str,
    ) -> None:
        warning = EpubExtractor.format_skipped_section_warning(source_chapter, reason)
        warnings.append(warning)
        significant_warnings.append(warning)

    @staticmethod
    def format_skipped_section_warning(source_chapter: EpubSourceChapter, reason: str) -> str:
        preview = EpubExtractor.ellipsize_text(
            EpubSectionSkipDetector.html_to_text_preview(source_chapter.html),
            100,
        )
        return EpubExtractor.format_warning(
            "Skipped EPUB section:",
            f"{source_chapter.href} ({reason}): {preview}",
        )

    @staticmethod
    def format_warning(label: str, detail: str) -> str:
        """Warning text with the variable detail dimmed; label stays in the default color."""
        return f"{label} {COL_DIM}{detail}{COL_DEFAULT}"

    @staticmethod
    def ellipsize_text(text: str, max_chars: int) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= max_chars:
            return text
        if max_chars <= 1:
            return "…"[:max_chars]
        return text[:max_chars - 1] + "…"

    @staticmethod
    def decode_item_content(item: Any) -> str:
        try:
            content = item.get_content()
        except Exception as e:
            EpubExtractor.log_warnings([EpubExtractor.format_warning("Unable to read EPUB item content:", str(e))])
            return ""
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        if isinstance(content, str):
            return content
        return ""

    @staticmethod
    def extract_title(html: str, fallback_href: str) -> str:
        BeautifulSoup = BeautifulSoupEpubChapterTextExtractor.import_beautiful_soup()
        soup = BeautifulSoup(html, "html.parser")
        for selector in ["h1", "h2", "title"]:
            tag = soup.find(selector)
            if tag:
                text = BeautifulSoupEpubChapterTextExtractor.normalize_inline_text(tag.get_text(" "))
                if text:
                    return text
        stem = os.path.splitext(os.path.basename(fallback_href))[0]
        return stem or fallback_href

    @staticmethod
    def copy_epub_to_project(epub_path: str, project_dir: str) -> str:
        dest_path = os.path.join(project_dir, PROJECT_TEXT_EPUB_FILE_NAME)
        if EpubExtractor.paths_reference_same_file(epub_path, dest_path):
            # Re-importing the saved project EPUB copy; it is already in place
            return ""
        try:
            shutil.copy(epub_path, dest_path)
            return ""
        except Exception as e:
            message = f"Error saving EPUB copy: {e}"
            L.e(message)
            return message

    @staticmethod
    def paths_reference_same_file(path_a: str, path_b: str) -> bool:
        if os.path.abspath(path_a) == os.path.abspath(path_b):
            return True
        try:
            return os.path.samefile(path_a, path_b)
        except OSError:
            return False

    @staticmethod
    def log_warnings(warnings: list[str]) -> None:
        for warning in warnings:
            try:
                L.w(strip_ansi_codes(warning))
            except Exception:
                pass