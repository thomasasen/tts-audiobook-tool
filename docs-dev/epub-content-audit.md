# EPUB Content Audit and Recovery

## Purpose

The EPUB importer must make content loss observable and reversible without modifying the source EPUB or changing the established Project V5 / book.v3 representation.

The implementation has five responsibilities:

1. preserve the EPUB spine reading order and navigation-defined logical sections;
2. explain every automatic omission with a stable identifier, reason, and short source preview;
3. allow the user to restore a recoverable omitted spine resource before chapter selection;
4. report structures that are flattened but may need human narration review; and
5. map source-text ranges to the phrase-group ranges produced by segmentation.

This is an import-time audit. It does not decrypt DRM, bypass access controls, rewrite the original EPUB, or infer missing content.

## Structured omission inventory

`EpubExtractor.load_source_chapters` returns an `EpubSourceChapterLoad`. Its iterable interface retains the historical four-value unpacking contract; the additional `skipped_sections` inventory is available as a named field.

Each `EpubSkippedSection` records:

- a deterministic opaque ID derived from the spine position, item ID, and package href;
- the publisher title and package-relative href;
- the skip category and human-readable reason;
- a bounded text preview;
- the original spine index;
- whether explicit restoration is supported; and
- whether the user currently chose to restore it.

Current categories distinguish table-of-contents resources, publication metadata, navigation-only documents, missing resources, unreadable resources, empty navigation anchors, and extracted sections without vocalizable text.

TOC and publication-metadata resources are recoverable. A navigation-only resource, missing item, unreadable document, or section with no vocalizable text is reported but is not offered as a restore action. The user cannot submit arbitrary filesystem paths: the API accepts only IDs already present in the project's import catalogue.

### Web Studio flow

The initial catalogue exposes the skipped-section inventory. The user can inspect the reason and preview and explicitly toggle a recoverable resource.

`PUT /api/projects/{project_id}/skipped-sections` accepts an `included_ids` list. The server validates those IDs against the original catalogue, reparses the unchanged source EPUB through the canonical extractor, and atomically replaces the sidecar catalogue. It preserves the selected start and exclusions by stable logical-chapter IDs where possible. A newly restored TOC remains excluded by default unless the user explicitly chooses it.

Changing the inventory returns the project to the editable draft state. It does not create or start a TTS job. The user then reviews the refreshed chapter order and saves chapter selection normally.

The original uploaded file remains `source.epub`; restoration changes only the derived import catalogue and, after explicit chapter confirmation, the generated Project V5 text.

## Navigation and mixed XHTML

The EPUB spine remains authoritative for reading order. EPUB 3 navigation anchors or the EPUB 2 NCX fallback define logical sections within that order.

A navigation resource that is also present in the spine is not automatically discarded in full. The importer requires at least one vocalizable paragraph or blockquote outside the `<nav>` subtree, but applies no minimum word count. A TOC heading by itself is not treated as reading prose. This avoids an arbitrary minimum-word threshold that could discard short publisher notes.

Navigation-only resources remain metadata and are not read aloud. An unresolved navigation target is a separate warning and does not silently reorder the spine.

## Structure handling and human review

The canonical extractor preserves the following structures in reading order:

| Source structure | Import behavior | Audit behavior |
| --- | --- | --- |
| Inline emphasis and strong text | Text is retained; display-style masks remain attached for preview | Existing formatting alignment checks remain active |
| Ordered list | Each list item receives its ordinal, including `start`, `value`, and reversed-list numbering | Nested unordered items retain a bullet marker |
| Unordered list | Items retain a bullet marker | No semantic rewrite |
| Table | Caption is retained; cells are emitted row-by-row with semicolon separators | A review item warns that header/cell relationships may need checking |
| Footnote | Footnote content remains in XHTML reading order; noteref text is retained | A review item points out that reference position and note order need human review |
| Figure and image | Figcaption remains text; image pixels and alt text are not inserted into the spoken text | A review item exposes the caption/alt preview and explains the omission |
| MathML | Textual tokens are retained in document order; no mathematical interpretation is attempted | A review item warns that a human-readable speaker form may be needed |
| `<hr>` scene break | Preserved as `* * *` | Segment coverage verifies that it was not lost |

The review inventory groups repeated structures of the same kind within a source document. Previews are bounded; the original XHTML remains the authoritative source.

### Optional speaker versions

A later speaker-version feature must be explicit and reviewable. The intended workflow is:

1. show the original source structure and the exact imported text side by side;
2. let the user request a speaker version for a selected table, formula, or figure;
3. present the proposed spoken wording as a separate preview, with its source reference;
4. require an explicit human accept/reject decision before it can replace or supplement the spoken span; and
5. retain the original source span and the accepted speaker text as separate, auditable data.

No automatic LLM paraphrase, guessed chart interpretation, invented formula expansion, or silent alt-text insertion is part of the current importer. The Project V5 source text and original EPUB are not modified by this audit feature.

## Source-to-spoken coverage

Each logical `EpubTextChapter` has a deterministic ID based on its navigation target or fallback spine href and title. These IDs are used to preserve user selections when a skipped spine item is restored.

After segmentation, each `EpubChapterCoverage` maps:

- the source chapter's half-open character range in the concatenated raw import text;
- the half-open phrase-group index range produced from that chapter;
- source and segmented character counts; and
- a normalized text-coverage result.

Coverage normalization collapses whitespace only. It does not remove punctuation, numbers, symbols, or words. A mismatch becomes a significant import warning and is visible in the Web Studio catalogue. The per-chapter mapping is stored in the private `chapters.json` sidecar; it does not add fields to Project V5 or alter `project_text_raw.txt`.

This is a chapter-to-segment mapping, not yet a character-level alignment through text normalization or model tokenization. A later NarrationPlan can refine it into exact source spans and normalized spoken spans without introducing a second EPUB parser.

## Regression strategy

`runpod/acceptance_epub.py` contains only self-authored synthetic books. The complex fixture exercises:

- spine order and a nested EPUB 3 navigation table;
- fragment-defined subsections in a single XHTML document;
- a mixed navigation document with a short prose paragraph;
- a TOC and publication-metadata omission with preview and restoration;
- inline strong/emphasis, a table, ordered and nested unordered lists;
- a footnote and noteref, a figure caption and alt text;
- MathML and an explicit scene break; and
- stable chapter identity, source immutability, and source-to-phrase-group coverage.

A synthetic EPUB passing CI is not evidence that every publisher-specific EPUB is lossless. At least one privately supplied complex EPUB still needs a user-side review before B01 can be marked fully accepted.
