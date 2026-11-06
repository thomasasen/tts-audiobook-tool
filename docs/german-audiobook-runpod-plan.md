# German nonfiction audiobook on RunPod — architecture and implementation plan

Status: **planning / not yet deployed or audio-tested**  
Branch: `feature/german-audiobook`  
Upstream: [zeropointnine/tts-audiobook-tool](https://github.com/zeropointnine/tts-audiobook-tool)

## Goal and constraints

Create a high-quality **German-language** audiobook from a legitimately held German EPUB of *The Qualified Sales Leader* (John McMahon). Target: **one male voice, warm and confident, lively but restrained**, natural enough for attentive listening to nonfiction. The voice must handle occasional English sales terms. Audio must **begin with the foreword**, not the EPUB navigation/table of contents, title pages or other front matter.

Use **RunPod**, keep the existing audiobook app and its export/validation capabilities, and prefer small upstream-friendly extensions over wholesale rewrites.

**Privacy and licensing:** Do not commit the copyrighted EPUB, extracted book text, generated full audiobook, Hugging Face credentials, cloned-speaker samples or RunPod credentials to this public repository. Keep these in ignored persistent volume paths. A German reference voice should be supported by explicit permission for voice synthesis; the [Thorsten-Voice](https://github.com/thorstenMueller/Thorsten-Voice) CC0 recordings are an initial candidate, not a guaranteed creative fit. Confirm terms for every TTS model and other downloaded data separately.

## Confirmed baseline from repository inspection

- `AGENTS.md` requires targeted reads, narrowly scoped tests, and reference to `docs-dev/test-routing.md`.
- The existing EPUB importer (`tts_audiobook_tool/text_ops/epub_extractor.py`) uses EbookLib and BeautifulSoup. It preserves spine order and creates logical sections from navigation; see `docs-dev/epub-import-flow.md`, `docs-dev/epub-navigation-sectioning.md`.
- `epub_section_skip_detector.py` already attempts to skip certain front matter and tables of contents, **but explicitly documents English-oriented heuristics**. This is not yet a reliable guarantee of "start at foreword" for an arbitrary German EPUB.
- An **inline whitespace repair mechanism** is already present in the extractor. Verify its behavior on EPUB drop caps before implementing a second repair strategy.
- The project already supports Qwen3-TTS and MOSS-TTS v1.5 locally. The repository pins their installations in separate `requirements-qwen3tts.txt` and `requirements-moss.txt` files (both Python 3.12; different PyTorch versions), so **separate venvs** are the safe starting point. Model switching/relaunch and VRAM behavior must be tested on RunPod.
- The app already provides project-level word substitutions; see `docs-dev/word-substitutions.md`. Reuse these before inventing a new glossary engine.
- Whisper-based STT validation, word-error alignment, segment retry and other checks exist; see `docs-dev/tts-validation-architecture.md`. `text_normalizer.py` has targeted English and Spanish treatments, but **no analogous German treatment**; language-aware precision needs additional tests.
- `docs-dev/test-routing.md` identifies relevant tests in `tests/text/` and specifies focused validation instead of default full-suite runs.

These are **static code/document findings only**. No successful RunPod deployment, inference, native-German listening test or full-EPUB import has been demonstrated in this branch.

## Architecture decisions

1. **Thin fork; upstream-first**: retain `master` as the upstream-aligned baseline; changes go to `feature/german-audiobook` and small reviewable PRs. Avoid modifying original engine implementations without demonstrated need.
2. **RunPod infrastructure**: reproducible Ubuntu/CUDA environment, `ffmpeg`/`libportaudio2` where required, Python 3.12, separate Qwen/MOSS environments, project and model caches on a persistent volume, idempotent startup scripts, health checks and explicit logs. Start with ~48 GB GPU VRAM as the safe comparison tier; benchmark less expensive GPUs only after measuring memory usage. Do not claim a verified instance type or price until checked.
3. **Explicit spoken-content start**: add an **opt-in, user-visible** EPUB start-section selection (by resolved navigation target or a stable logical-section identifier). Show preview of the first spoken lines and retained sections. For this book, select the *Dev Ittycheria foreword*, reported in the supplied EPUB under `index_split_004.html`. Do not hardcode that filename or treat it as a cross-book invariant. Preserve original ordering of subsequent chapters; avoid dropping epigraphs or useful explanatory prefaces without an explicit choice.
4. **Text hygiene**: Unicode normalization, whitespace joining around inline markup/drop caps, repeated header/footer detection, heading cleanup, citation/footnote policy, and a before/after preview with a conservative audit log. Changes must not silently alter semantic content. Test on synthetic EPUB fixtures and finally on the user's local book; do not place book text in the repository.
5. **Pronunciation**: use native German reference samples, German book language code (`de`), existing word-substitution feature, and a separately maintained glossary/profile for business terms (e.g. MEDDPICC, QBR, CRO, Champion, Economic Buyer, Forecast). Pronunciation hints are **model-specific**, previewable and reversible; do not indiscriminately convert English terms to phonetic German.
6. **Voice quality comparison**: A/B compare Qwen3-TTS 1.7B Base and MOSS-TTS v1.5 on the **same German speaker reference** and the same sample passages. Use at least: reflective foreword, complex nonfiction paragraph, English-term/code-switch passage, dialogue/list, and a 15–20 minute consistency test. Prefer one narrator; no automatic dramatic multi-cast. Compare correct pronunciation, pauses, liveliness, natural breathing, stability, hallucinations/omissions, and listener fatigue. Choose model only after actual listening.
7. **German STT quality gate**: expand normalization and equivalence cautiously for umlauts, ß, numbers, compound words, acronyms, English interspersed with German, and spelling variants. Distinguish actual omissions/repetitions from Whisper recognition errors. Track false positives and false negatives using known test cases; do not treat zero WER as perfect prosody.
8. **Export and audit**: existing chapter-aware FLAC/M4B and browser player are preferred. Verify opening audible sentence, chapter metadata/order, long pauses, clipping and integrated loudness, speech/text alignment, retries and resumability. Never normalize so aggressively that natural dynamics disappear.

## Implementation sequence and acceptance gates

### P0 — RunPod baseline and model reproducibility
- Add versioned bootstrap, dependency pin policy, persistent directory and secret handling; include a smoke test for CUDA/ffmpeg/portaudio and log paths.
- Install and launch *unmodified* app; run a licensed synthetic German sentence via Qwen and MOSS on the target GPU; record versions, memory, generation speed and any failures.
- **Gate:** cold-start and restart work without reinstalling all models; files survive restart; no secrets in Git.

### P1 — EPUB selection and text correctness
- Reuse importer and navigation sectioning. Add opt-in start-at-foreword selection plus preview and precise skip list.
- Test German TOC headings, inline drop-cap joining, sections spanning XHTML documents, same-document anchors, non-reading front matter, final chapter retention.
- **Gate:** first spoken phrase is from the selected foreword, and every retained reading section is in the correct order. No table of contents narration.

### P2 — German narrator profile and evaluation
- Review Thorsten-Voice CC0 recording quality and provenance; select clean male reference clips, or obtain a licensed professional German voice if necessary.
- Build business-term test set and pronunciation substitutions with per-model overrides. Generate 2–5 minute side-by-side clips, then 15–20 minutes with shortlisted profiles.
- **Gate:** human listening approval of natural German voice and restrained engaging delivery, with no obvious foreign accent.

### P3 — German validation and full-book generation
- Add German normalization/word equivalence test cases, stage STT with retry and targeted manual QA.
- Run chapter-wise generation with restart checkpoints; export M4B, inspect chapter metadata and beginning/end, verify no missing or duplicated passages.
- **Gate:** complete audiobook passes audio and text-integrity QA; source files never enter public Git.

## Tests / rollout discipline

Start from `tests/text/test_epub_extractor.py`, `tests/text/test_epub_section_skip_detector.py`, `tests/text/test_prompt_normalizer.py`, `tests/text/test_text_normalizer.py`, `tests/text/test_word_equivalence.py`, plus the validation tests identified through `docs-dev/test-routing.md`. Run direct unit tests on each change, integration tests for affected contracts, then a RunPod smoke test. Report skips, untested GPU paths, real-time generation factor and listening findings separately. Do not report a plan or documentation commit as implemented functionality.

## Not in scope now

No new web UI, no book-specific production content in source control, no training a new foundation TTS model, no modifications to `master` before review, and no claims that model choice or pricing are settled before benchmarking.

## Tracking issues

- [#2 — P0 RunPod baseline and reproducible model setup](https://github.com/thomasasen/tts-audiobook-tool/issues/2)
- [#3 — P1 EPUB foreword selection and German text cleanup](https://github.com/thomasasen/tts-audiobook-tool/issues/3)
- [#4 — P2 Native-German male narrator, pronunciation and voice A/B test](https://github.com/thomasasen/tts-audiobook-tool/issues/4)
- [#5 — P3 German STT validation and M4B export](https://github.com/thomasasen/tts-audiobook-tool/issues/5)

## Immediate next step

Create focused implementation issues for P0–P3. Begin with **P0** reproducible RunPod baseline; preserve this plan as the current architecture decision record, then only change scope when new benchmark or EPUB findings justify it.
