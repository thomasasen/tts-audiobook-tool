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
    ("49,90 EUR", "neunundvierzig Euro und neunzig Cent"),
    ("1,00 Euro", "ein Euro"),
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



# B02 v3: these expectations are authored independently of implementation.
from types import SimpleNamespace
from tts_audiobook_tool.text_ops.german_speech_normalizer import (
    V2_VERSION, normalize_german_speech_v3, validate_german_glossary,
)


@pytest.mark.parametrize(("source", "expected"), [
    ("1,25", "eins Komma zwei fünf"),
    ("1.234", "eintausendzweihundertvierunddreißig"),
    ("49,90 €", "neunundvierzig Euro und neunzig Cent"),
    ("3,5 %", "drei Komma fünf Prozent"),
    ("08:30 Uhr", "acht Uhr dreißig"),
    ("am 3. Tag", "am dritten Tag"),
    ("zum 2. Mal", "zum zweiten Mal"),
    ("08:00 Uhr", "acht Uhr"),
    ("§ 12", "Paragraf zwölf"),
    ("§§ 12–14", "Paragrafen zwölf bis vierzehn"),
    ("-3", "minus drei"),
    ("−2,5", "minus zwei Komma fünf"),
    ("-49,90 €", "minus neunundvierzig Euro und neunzig Cent"),
    ("0,05 €", "fünf Cent"),
    ("1,01 €", "ein Euro und ein Cent"),
    ("12 €", "zwölf Euro"),
    ("12,00 €", "zwölf Euro"),
    ("1 %", "ein Prozent"),
    ("0 %", "null Prozent"),
    ("1.234,56 €", "eintausendzweihundertvierunddreißig Euro und sechsundfünfzig Cent"),
    ("Im Jahr 2026.", "Im Jahr zweitausendsechsundzwanzig."),
    ("2020–2026", "zweitausendzwanzig bis zweitausendsechsundzwanzig"),
    ("z.B. gilt u.a. das CRM.", "zum Beispiel gilt unter anderem das CRM."),
    ("3,5 Prozent", "drei Komma fünf Prozent"),
    ("d. h. 3,5 %", "das heißt drei Komma fünf Prozent"),
    ("01.02.2026", "erster Februar zweitausendsechsundzwanzig"),
    ("am 15.11.2026", "am fünfzehnten November zweitausendsechsundzwanzig"),
])
def test_v3_german_golden_cases(source, expected):
    assert normalize_german_speech_v3(source, {}) == expected


@pytest.mark.parametrize("unchanged", [
    "https://example.org/2026/1.234?version=2.0.1",
    "https://www.example.org/path?q=CRM",
    "sales2026@example.com",
    "www.example.de/1.234",
    "192.168.1.12",
    "v1.2.3",
    "Version 1.234",
    "ISBN 978-3-16-148410-0",
    "2026-11-08",
    "29.02.2025",  # invalid calendar date
    "12:75",  # no valid Uhr qualifier
    "08:30",
    "000123",
    "007",
    "1. Kapitel",
    "2. Die zweite Überschrift",
    "1.2",
    "1,234 €",  # non-cent precision is not silently reinterpreted
    "SKU2026",
    "CRM2026",
    "MEDDIC",
    "MEDDPICC",
    "CRM",
    "ERP",
    "SaaS",
    "PaaS",
    "IaaS",
    "ARR",
    "MRR",
    "NRR",
    "ROI",
    "TCO",
    "EBITDA",
    "SLA",
    "QBR",
    "POC",
])
def test_v3_conservative_protection_and_technical_vocabulary(unchanged):
    assert normalize_german_speech_v3(unchanged, {}) == unchanged


def test_v3_multilingual_sales_terms_preserve_spelling_unless_explicitly_overridden():
    text = (
        "Der Economic Buyer bespricht den Business Case im Customer Success "
        "Meeting mit dem Account Executive. Proof of Concept, Go-to-Market, "
        "Forecasting, Champion, Discovery und Pipeline."
    )
    assert normalize_german_speech_v3(text, {}) == text


def test_v3_glossary_longest_match_punctuation_case_and_nonrecursive():
    source = '„Customer Success Manager“, Customer Success! CRM und myCRM.'
    glossary = {
        "Customer Success": "Kundenerfolg",
        "Customer Success Manager": "Leitung Kundenservice",
        "CRM": "Kundenverwaltung",
        "Kundenverwaltung": "Darf nicht erneut eingesetzt werden",
    }
    result = normalize_german_speech_v3(source, glossary)
    assert result == '"Leitung Kundenservice", Kundenerfolg! Kundenverwaltung und myCRM.'
    # One invocation is nonrecursive; chaining two separate invocations is not a supported guarantee.
    assert "Darf nicht erneut" not in result
    assert source.startswith("„Customer")
    assert glossary["CRM"] == "Kundenverwaltung"


def test_v3_protected_span_never_gets_glossary_and_spoken_override_is_opaque():
    text = "CRM https://example.com/CRM salesCRM@example.com 12 %"
    result = normalize_german_speech_v3(text, {"CRM": "Customer 7"})
    assert result == "Customer 7 https://example.com/CRM salesCRM@example.com zwölf Prozent"


def test_v3_glossary_can_override_model_specific_acronym_pronunciation():
    assert normalize_german_speech_v3(
        "Die SaaS-Plattform ist Teil der Customer-Success-Strategie.",
        {"SaaS": "Säss", "Customer-Success": "Kundenbetreuung"},
    ) == "Die Säss-Plattform ist Teil der Kundenbetreuung-Strategie."


@pytest.mark.parametrize("invalid", [
    {"": "eins"},
    {"CRM": ""},
    {"CRM": " "},
    {" CRM": "Eins"},
    {"CRM ": "Eins"},
    {"CRM": "\n"},
    {"CRM": "<voice>"},
    {"CRM": "eins", "crm": "zwei"},
    {"Customer Success": "Erfolg", "Customer  Success": "Zweites"},
    {"!!!": "Wort"},
    {"a": "b"} | {f"term{i}": "okay" for i in range(500)},
])
def test_v3_glossary_invalid_rules_rejected(invalid):
    with pytest.raises(ValueError):
        validate_german_glossary(invalid)


def test_v3_non_text_inputs_and_large_glossary():
    with pytest.raises(TypeError):
        normalize_german_speech_v3(None, {})
    with pytest.raises(ValueError):
        normalize_german_speech_v3("CRM", {"a": ""})
    glossary = {f"Term{i}": f"Ausdruck{i}" for i in range(500)}
    text = " ".join(["Term499", "100 €"] * 200)
    result = normalize_german_speech_v3(text, glossary)
    assert result.count("Ausdruck499") == 200
    assert result.count("einhundert Euro") == 200


def test_v3_unicode_soft_hyphen_and_idempotence_on_unambiguous_text():
    text = "Die Mar\u00adge beträgt 1,25\u00a0%. § 12 ist relevant."
    first = normalize_german_speech_v3(text, {})
    assert first == "Die Marge beträgt eins Komma zwei fünf Prozent. Paragraf zwölf ist relevant."
    assert normalize_german_speech_v3(first, {}) == first
    assert normalize_german_speech_v3("", {}) == ""


def test_v3_project_v5_manifest_text_remains_verbatim(tmp_path, monkeypatch):
    from tts_audiobook_tool.web_studio import production
    stored = {
        "format": "book.v3",
        "book": {"sections": [{
            "title": "Wirtschaftlichkeit",
            "phrase_groups": [
                {"phrases": [{"text": "Die Marge: "}, {"text": "49,90 € im Jahr 2026."}]},
                {"phrases": [{"text": "CRM und SaaS."}]},
            ],
        }]},
    }
    engine = tmp_path / "engine"
    engine.mkdir()
    source_path = engine / "project_text.json"
    source_path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(production, "_studio", lambda: SimpleNamespace(_project_dir=lambda _id: tmp_path))
    original_bytes = source_path.read_bytes()
    segments = production._manifest("synthetic-project")
    assert [s["text"] for s in segments] == [
        "Die Marge: 49,90 € im Jahr 2026.", "CRM und SaaS."
    ]
    prompts = [production._narration_prompt(s["text"], {}, production.DE_NORMALIZER_VERSION)
               for s in segments]
    assert prompts == [
        "Die Marge: neunundvierzig Euro und neunzig Cent im Jahr zweitausendsechsundzwanzig.",
        "CRM und SaaS.",
    ]
    assert source_path.read_bytes() == original_bytes


def test_v3_preview_production_share_one_normalizer_and_fingerprints():
    segment = {"text": "Der Business Case beträgt 49,90 €."}
    glossary = {"Business Case": "Geschäftsfall"}
    v3 = production.DE_NORMALIZER_VERSION
    spoken = production._narration_prompt(segment["text"], glossary, v3)
    assert spoken == "Der Geschäftsfall beträgt neunundvierzig Euro und neunzig Cent."
    assert spoken == production._normalize_de(segment["text"], glossary)
    assert spoken == production._narration_prompt(segment["text"], glossary, v3)
    plan = {"glossary": glossary, "normalizer_version": v3, "model_identity": "model-abc"}
    other_glossary = {**plan, "glossary": {"Business Case": "Wirtschaftlichkeitsprüfung"}}
    assert production._segment_fingerprint(segment, plan) != production._segment_fingerprint(
        segment, other_glossary
    )
    assert production._segment_fingerprint(segment, plan) != production._segment_fingerprint(
        segment, {**plan, "normalizer_version": V2_VERSION}
    )
    assert production._segment_fingerprint(segment, plan) != production._segment_fingerprint(
        segment, {**plan, "model_identity": "model-new"}
    )


def test_v3_v2_and_legacy_dispatch_stable_for_saved_jobs():
    text = "3,5 % sind 49,90 €."
    plan = {"glossary": {}, "model_identity": "model-abc"}
    assert production._narration_prompt(text, {}, V2_VERSION) == normalize_german_speech(text, {})
    assert production._narration_prompt(text, {}, None) == production._normalize_de_legacy(text, {})
    assert production._segment_fingerprint({"text": text}, {**plan, "normalizer_version": V2_VERSION}) == hashlib.sha256(
        (text + "\0" + json.dumps({}, sort_keys=True) + "\0" + production.VOICE_ID
         + "\0model-abc\0" + V2_VERSION).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="Unbekannte Normalisierungsversion"):
        production._narration_prompt(text, {}, "de-narration-v99")



def test_v3_product_names_with_digits_support_explicit_full_glossary_matches():
    source = "Die SAP S/4HANA-Integration nutzt Term499 und CRM2026."
    glossary = {
        "SAP S/4HANA": "SAP ERP",
        "Term499": "Sonderausdruck",
        "CRM": "Kundenverwaltung",
    }
    assert normalize_german_speech_v3(source, glossary) == (
        "Die SAP ERP-Integration nutzt Sonderausdruck und CRM2026."
    )


def test_v3_glossary_never_crosses_protected_email_url_or_identifier_fragments():
    source = "CRM crm@example.com CRM2026 https://example.com/CRM 1. Kapitel"
    glossary = {"CRM": "System", "CRM202": "verkürzt", "1. Kapitel": "unsicher"}
    result = normalize_german_speech_v3(source, glossary)
    assert result == "System crm@example.com CRM2026 https://example.com/CRM 1. Kapitel"


def test_v3_interleaved_date_technical_token_glossary_and_currency():
    source = "Am 15.11.2026 erreicht CRM2026 mit ARR 49,90 €. Siehe www.example.org/49,90."
    glossary = {"ARR": "jährlich wiederkehrender Umsatz"}
    assert normalize_german_speech_v3(source, glossary) == (
        "Am fünfzehnten November zweitausendsechsundzwanzig erreicht CRM2026 "
        "mit jährlich wiederkehrender Umsatz neunundvierzig Euro und neunzig Cent. "
        "Siehe www.example.org/49,90."
    )
