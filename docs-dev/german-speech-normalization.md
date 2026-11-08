# B02: German speech-prompt normalization (Audiobook Studio)

## Scope and invariants

The Studio normalizes **spoken prompts**, not the uploaded EPUB or canonical
Project V5 `book.v3` phrase groups. No translation, speculative English
pronunciation dictionary, model-specific control tokens, or duplicate EPUB
parser is introduced. The existing global punctuation and whitespace
normalization remains in `PromptNormalizer.normalize_prompt(..., language_code="")`;
German numeric expansion is handled before that call to avoid general
per-digit replacements.

**Version contract (do not change without a migration plan):**

| Stored `normalizer_version` | Prompt generator | Fingerprint |
| --- | --- | --- |
| Missing / null | `production._normalize_de_legacy` | Original, unversioned SHA-256 formula |
| `de-narration-v2` | Frozen `normalize_german_speech` | Original v2 SHA-256 formula |
| `de-narration-v3` | `normalize_german_speech_v3` | Versioned v3 SHA-256 formula |
| Unknown | Reject the job | Reject before cache reuse |

Only **new** Studio jobs use v3. Existing job plans and already synthesized
WAVs are never auto-migrated. The source text, exact sorted JSON glossary,
speaker/model identity, and normalizer version contribute to the segment cache
fingerprint. A different glossary or code revision that changes the prompt
requires a new normalizer version; model binary/inference identity already
changes on model revisions. Old v2 and unversioned hash bytes remain stable.

The authenticated, private per-segment JSON sidecar already records
`text` (source) versus `prompt` (what the worker actually received).
New v3 sidecars also include `normalizer_version`. Do **not** copy these
sidecars or book passages to public logs, GitHub Issues, CI artifacts or
telemetry. Log operational counters and rule versions only.

## Deterministic rules (v3)

- Read unambiguous German decimal fractions digit by digit after "Komma".
- Read canonical German thousands groups and integers with conservative
  length bounds. Leading-zero identifiers, ambiguous dotted numbers and
  numbers with unknown precision are not guessed.
- Render whole euros, euros plus cents, plain cents and percentage values.
  Preserve non-cent monetary precision rather than reinterpret the amount.
- Read valid numeric calendar dates with German month names and ordinal day
  forms; invalid dates remain unchanged. The explicit prepositions "am" and
  "vom" select dative. Other inflection contexts are not guessed.
- Read valid `HH:MM Uhr` times; bare time-like identifiers remain unchanged.
- Read `§ 12` and `§§ 12–14` as paragraph references.
- Preserve enumeration labels such as `1. Kapitel` instead of confusing them
  with decimal numbers or choosing the wrong ordinal inflection.
- Read four-digit years and explicit ranges as German numbers.
- Expand a small, already supported set of common abbreviations.
- URLs, email addresses, IP addresses, dotted versions, ISBN tokens,
  ISO dates, alphanumeric technical identifiers and ambiguous tokens are
  protected before all vocabulary and numeric rules.

No fixed pronunciations are imposed on MEDDIC, MEDDPICC, CRM, ERP, SaaS,
PaaS, IaaS, ARR, MRR, NRR, ROI, TCO, EBITDA, SLA, QBR, POC or mixed
German/English job titles. Their phonetic realization depends on the installed
TTS model. Where an audible error is verified, use a deliberate, project-level
glossary override. The current Thorsten adapter does **not** implement
explicit phoneme input or multilingual language switching. A text-only
test cannot establish accent or phonetic fidelity.

## Glossary and user-facing preview

The saved `word_substitutions` map is unchanged. New edits are validated for
size, nonempty terms/forms, control characters, HTML-like value brackets and
case-insensitive collisions. Matching is case-insensitive, Unicode-aware,
word-boundary anchored, longest-first and single-pass. Multiword phrases and
hyphenated technical phrases are supported. Protected technical spans cannot
be overridden accidentally; replacement text is emitted as supplied (with
initial capitalization adapted to a capitalized source) and is not fed back
into numeric or glossary substitution within the same invocation.

The neutral sample voice audition still uses the curated sample sentences
and has **no book-specific glossary**. It now runs the same v3 normalization
dispatch as a new production job. As B04 is not yet implemented, this is not
a book-dependent pronunciation test. B04 must reuse the
`production._narration_prompt` dispatcher with the selected project's
glossary and frozen job version, rather than adding an independent preview
normalizer.

## Tests and constraints

Focused CPU-only tests live in
`tests/text/test_german_speech_normalizer.py`. They include independently
authored German expected results, negative identifier cases, overlapping
glossary terms, Unicode, idempotence for unambiguous non-glossary prompts,
long text, a synthetic `book.v3` manifest, legacy and v2 hashes, and
preview/production dispatch. CI uses the existing German Audiobook Studio
backend job; broader API/EPUB/browser workflows continue to validate
consumer contracts.

There is no external speech inference, paid GPU run, human listening
comparison, or model-specific English phoneme validation in B02. Those
remain dependent on subsequent B04/D02/E03 acceptance work.
