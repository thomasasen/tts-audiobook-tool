"""Golden cases for German expert-book prompts, without real/private voices."""
from __future__ import annotations

import hashlib
import json

import pytest
from num2words import num2words

from tts_audiobook_tool.text_ops.german_speech_normalizer import (
    VERSION, normalize_german_speech,
)
from tts_audiobook_tool.web_studio import production


@pytest.mark.parametrize(("source", "expected"), [
    ("1,25", "eins Komma zwei fünf"),
    ("3,5 %", "drei Komma fünf Prozent"),
    ("49,90 €", "neunundvierzig Euro und neunzig Cent"),
    ("1 €", "ein Euro"),
    ("1,00 €", "ein Euro"),
    ("§ 12", "Paragraf zwölf"),
    ("§§ 12 und 13", "Paragrafen zwölf und dreizehn"),
    ("z. B. CRM und u. a. SaaS", "zum Beispiel CRM und unter anderem SaaS"),
])
def test_german_golden_expressions(source: str, expected: str):
    assert normalize_german_speech(source, {}) == expected


def test_thousands_and_year_use_de_num2words_not_digit_by_digit():
    expected = num2words(1234, lang="de")
    assert normalize_german_speech("1.234", {}) == expected
    assert normalize_german_speech("Im Jahr 2026.", {}) == (
        "Im Jahr " + num2words(2026, lang="de") + "."
    )


def test_protected_times_dates_urls_email_and_ambiguous_acronyms_are_unchanged():
    source = "08:30, 15.11.2026, https://example.com/123, service99@example.com, ARR und NRR."
    assert normalize_german_speech(source, {}) == source
    # Non-numeric identifiers are not turned into imagined readings.
    for item in ("CRM", "SaaS", "MEDDPICC", "ARR", "NRR", "ROI", "SLA", "EBITDA"):
        assert normalize_german_speech(item, {}) == item


def test_longest_multiword_glossary_wins_and_source_is_untouched():
    source = "Customer Success nutzt SaaS. CUSTOMER SUCCESS verbessert CRM."
    glossary = {
        "Customer": "Kunde",
        "Customer Success": "Kundenbetreuung",
        "SaaS": "Säss",
    }
    result = normalize_german_speech(source, glossary)
    assert result == "Kundenbetreuung nutzt Säss. Kundenbetreuung verbessert CRM."
    assert source == "Customer Success nutzt SaaS. CUSTOMER SUCCESS verbessert CRM."
    assert glossary["Customer Success"] == "Kundenbetreuung"
    assert normalize_german_speech(result, glossary) == result


def test_normalization_version_separates_new_jobs_without_invalidating_old_hash():
    segment = {"text": "Der Preis beträgt 49,90 €."}
    base = {"glossary": {"SaaS": "Säss"}, "model_identity": "mock-model-sha"}
    expected_legacy = hashlib.sha256((
        segment["text"] + "\0" + json.dumps(base["glossary"], sort_keys=True)
        + "\0" + production.VOICE_ID + "\0" + base["model_identity"]
    ).encode()).hexdigest()
    assert production._segment_fingerprint(segment, base) == expected_legacy
    upgraded = {**base, "normalizer_version": VERSION}
    assert production._segment_fingerprint(segment, upgraded) != expected_legacy
    with pytest.raises(ValueError, match="Unbekannte Normalisierungsversion"):
        production._segment_fingerprint(
            segment, {**base, "normalizer_version": "unknown-rule-v9"}
        )


def test_legacy_prompt_path_remains_available_for_saved_jobs():
    original = "CRM ist wichtig."
    assert production._normalize_de_legacy(original, {}) == (
        production._normalize_de_legacy(original, {})
    )
    assert production._normalize_de("1,25", {}) == "eins Komma zwei fünf"
