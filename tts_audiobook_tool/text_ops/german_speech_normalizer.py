"""Conservative German speech-prompt transformations, never EPUB mutations.

This module is deliberately model-agnostic. Ambiguous identifiers, times,
dates, URLs and e-mail addresses are left to an explicitly audited glossary
or the speaker model rather than guessed.
"""
from __future__ import annotations

import re

from num2words import num2words

from tts_audiobook_tool.text_ops.prompt_normalizer import PromptNormalizer

V2_VERSION = "de-narration-v2"
VERSION = "de-narration-v3"

# Protected input is not modified by number or glossary rules. Group
# separators, punctuation and source character offsets are left unchanged
# in the Project V5 book and in the EPUB itself.
_PROTECTED = re.compile(
    r"https?://[^\s]+|www\.[^\s]+|"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b|"
    r"\b\d{1,2}:\d{2}\b",
    flags=re.IGNORECASE | re.UNICODE,
)
_UNIT = re.compile(
    r"(?<![\w.,:/-])(?P<amount>(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?)\s*(?P<unit>€|%)(?!\w)",
    re.UNICODE,
)
_DECIMAL = re.compile(r"(?<![\w.,:/-])(\d+),(\d+)(?![\w.,:/-])", re.UNICODE)
_GROUPED = re.compile(r"(?<![\w.,:/-])\d{1,3}(?:\.\d{3})+(?![\w.,:/-])", re.UNICODE)
_INTEGER = re.compile(r"(?<![\w.,:/-])\d+(?![\w,:/-]|\.\d)", re.UNICODE)
_ABBREVIATIONS = (
    (re.compile(r"(?<!\w)z\.\s*B\.(?!\w)", re.IGNORECASE), "zum Beispiel"),
    (re.compile(r"(?<!\w)d\.\s*h\.(?!\w)", re.IGNORECASE), "das heißt"),
    (re.compile(r"(?<!\w)u\.\s*a\.(?!\w)", re.IGNORECASE), "unter anderem"),
)


def _number(value: str) -> str:
    clean = value.replace(".", "")
    if not clean.isdigit() or len(clean) > 8:
        return value
    return num2words(int(clean), lang="de")


def _decimal(value: str) -> str:
    whole, fraction = value.split(",", 1)
    if len(whole.replace(".", "")) > 8 or len(fraction) > 4:
        return value
    return _number(whole) + " Komma " + " ".join(
        num2words(int(digit), lang="de") for digit in fraction
    )


def _unit(match: re.Match[str]) -> str:
    value, unit = match.group("amount"), match.group("unit")
    if unit == "%":
        spoken = _decimal(value) if "," in value else _number(value)
        return spoken + " Prozent"
    if "," not in value:
        whole = value.replace(".", "")
        return ("ein" if whole == "1" else _number(whole)) + " Euro"
    whole, cents = value.split(",", 1)
    euro = ("ein" if int(whole.replace(".", "")) == 1 else _number(whole)) + " Euro"
    cents = cents.ljust(2, "0")
    if cents == "00":
        return euro
    cent_word = "ein" if int(cents) == 1 else _number(cents)
    return euro + " und " + cent_word + (" Cent" if int(cents) != 1 else " Cent")


def _substitute_glossary(text: str, glossary: dict[str, str]) -> str:
    # One longest-match pass prevents overlapping one- and multiword entries
    # from changing each other's replacement text. Escape all user keys.
    entries = {key.casefold(): value for key, value in glossary.items() if key and value}
    if not entries:
        return text
    alternates = sorted(entries, key=lambda key: (-len(key), key))
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(key) for key in alternates) + r")(?!\w)",
        re.IGNORECASE | re.UNICODE,
    )
    def substitute(match: re.Match[str]) -> str:
        value = entries[match.group(0).casefold()]
        if match.group(0)[:1].isupper() and value[:1].islower():
            return value[0].upper() + value[1:]
        return value
    return pattern.sub(substitute, text)


def _transform(text: str, glossary: dict[str, str]) -> str:
    text = _substitute_glossary(text, glossary)
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"§§\s*", "Paragrafen ", text)
    text = re.sub(r"§\s*", "Paragraf ", text)
    text = _UNIT.sub(_unit, text)
    text = _DECIMAL.sub(lambda m: _decimal(m.group(0)), text)
    text = _GROUPED.sub(lambda m: _number(m.group(0)), text)
    # Keep long numbers (ISBN, tracking IDs) unchanged instead of
    # hallucinating semantic pronunciation.
    text = _INTEGER.sub(lambda m: _number(m.group(0)) if len(m.group(0)) <= 4 else m.group(0), text)
    return text


def normalize_german_speech(text: str, glossary: dict[str, str]) -> str:
    if not text:
        return ""
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    pieces: list[str] = []
    cursor = 0
    for protected in _PROTECTED.finditer(text):
        pieces.append(_transform(text[cursor:protected.start()], glossary))
        pieces.append(protected.group(0))
        cursor = protected.end()
    pieces.append(_transform(text[cursor:], glossary))
    # Reuse the canonical whitespace/punctuation pipeline without its
    # broad per-digit conversion, which corrupts German decimal tokens.
    return PromptNormalizer.normalize_prompt("".join(pieces), language_code="")


# B02/v3: operate only on the *speech prompt*. Retain the v2 implementation
# above byte-for-byte for jobs that already recorded de-narration-v2.
from datetime import date
from functools import lru_cache

_MONTHS_V3 = (
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)
# Protected tokens are found BEFORE glossary and number substitutions. The
# explicit date group is the only exception: valid German dates are spoken,
# invalid dates are passed through untouched.
_PROTECTED_V3 = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s<>\"']+)|"
    r"(?P<email>\b[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}\b)|"
    r"(?P<date>\b\d{1,2}\.\d{1,2}\.\d{4}\b)|"
    r"(?P<list>(?<!\w)\d{1,3}\.(?=\s+))|"
    r"(?P<precision>\b\d+,\d{3,}\s*€)|"
    r"(?P<technical>"
    r"\b(?:ISBN(?:-1[03])?\s*[: ]\s*[0-9Xx-]{10,}|"
    r"Version\s+\d+(?:\.\d+)+|"
    r"(?:\d{1,3}\.){3}\d{1,3}|"
    r"(?:v?\d+\.){2,}\d+(?:[-+][\w.]+)?|"
    r"\d{4}-\d{2}-\d{2}|"
    r"[A-Za-zÄÖÜäöüß][\w.-]*\d[\w.-]*|"
    r"\d+[A-Za-zÄÖÜäöüß][\w.-]*|"
    r"\d{1,2}:\d{2}(?!\s*Uhr\b))\b)",
    re.IGNORECASE | re.UNICODE,
)
_V3_UNIT = re.compile(
    r"(?<![\w.,:/-])(?P<sign>[-−]?)(?P<amount>(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,4})?)"
    r"\s*(?P<unit>€|%)(?!\w)",
)
_V3_TIME = re.compile(r"(?<![\w:])(?P<h>\d{1,2}):(?P<m>\d{2})\s*Uhr\b", re.IGNORECASE)
_V3_PARAGRAPH_RANGE = re.compile(r"(?<!\w)§{1,2}\s*(\d{1,4})\s*[–—-]\s*(\d{1,4})(?!\w)")
_V3_PARAGRAPH = re.compile(r"(?<!\w)(§§?)\s*(\d{1,4})(?!\w)")
_V3_YEAR_RANGE = re.compile(r"(?<![\w./-])((?:19|20)\d{2})\s*[–—-]\s*((?:19|20)\d{2})(?![\w./-])")
_V3_DECIMAL = re.compile(r"(?<![\w.,:/-])([-−]?\d+),(\d{1,4})(?![\w.,:/-])")
_V3_GROUPED = re.compile(r"(?<![\w.,:/-])([-−]?\d{1,3}(?:\.\d{3})+)(?![\w.,:/-])")
_V3_INTEGER = re.compile(r"(?<![\w.,:/-])([-−]?)(\d+)(?![\w,:/-]|\.\d)")


def validate_german_glossary(glossary: dict[str, str]) -> None:
    """Reject ambiguous new rules; do not migrate or rewrite stored glossaries."""
    if not isinstance(glossary, dict) or len(glossary) > 500:
        raise ValueError("Das Glossar muss eine Zuordnung mit höchstens 500 Einträgen sein.")
    seen: set[str] = set()
    for key, value in glossary.items():
        if (
            not isinstance(key, str) or not isinstance(value, str)
            or not 1 <= len(key) <= 100 or not 1 <= len(value) <= 180
            or key != key.strip() or not key.strip() or not value.strip()
            or not any(char.isalnum() for char in key)
            or any(ord(char) < 32 for char in key + value)
            or "<" in value or ">" in value
        ):
            raise ValueError("Das Glossar enthält einen ungültigen Begriff oder eine ungültige Sprechform.")
        folded = key.casefold()
        if folded in seen:
            raise ValueError("Glossareinträge unterscheiden sich nur durch Groß-/Kleinschreibung.")
        seen.add(folded)


@lru_cache(maxsize=32)
def _compiled_glossary_v3(keys: tuple[str, ...]) -> re.Pattern[str] | None:
    if not keys:
        return None
    # Match longer phrases first, once. Do not touch replacement text again.
    alternatives = [re.escape(key).replace(r"\ ", r"\s+") for key in
                    sorted(keys, key=lambda s: (-len(s), s))]
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


def _spoken_integer_v3(value: str, *, leading_zero: bool = False) -> str:
    negative = value.startswith(("-", "−"))
    digits = value.lstrip("-−").replace(".", "")
    if (
        not digits.isascii() or not digits.isdigit() or len(digits) > 8
        or (len(digits) > 1 and digits[0] == "0" and not leading_zero)
    ):
        return value
    spoken = num2words(int(digits), lang="de")
    return ("minus " if negative else "") + spoken


def _spoken_decimal_v3(value: str) -> str:
    negative = value.startswith(("-", "−"))
    plain = value.lstrip("-−")
    whole, fraction = plain.split(",", 1)
    spoken_whole = _spoken_integer_v3(whole)
    if not 1 <= len(fraction) <= 4 or (len(whole) > 1 and whole.startswith("0")) or len(whole) > 8:
        return value
    return ("minus " if negative else "") + spoken_whole + " Komma " + " ".join(
        num2words(int(digit), lang="de") for digit in fraction
    )


def _spoken_unit_v3(match: re.Match[str]) -> str:
    amount, unit = match.group("amount"), match.group("unit")
    sign = "minus " if match.group("sign") else ""
    if unit == "%":
        spoken = _spoken_decimal_v3(amount) if "," in amount else _spoken_integer_v3(amount)
        if spoken == amount and (len(amount) > 8 or amount.startswith("0") and len(amount) > 1):
            return match.group(0)
        return sign + ("ein" if amount == "1" else spoken) + " Prozent"
    if "," in amount:
        euros, cents = amount.split(",", 1)
        if len(cents) > 2 or len(euros.replace(".", "")) > 8:
            return match.group(0)
        cents = cents.ljust(2, "0")
    else:
        euros, cents = amount, "00"
    euro_num = _spoken_integer_v3(euros)
    if euro_num == euros and (len(euros.replace(".", "")) > 8 or len(euros) > 1 and euros.startswith("0")):
        return match.group(0)
    euro_word = "ein" if int(euros.replace(".", "")) == 1 else euro_num
    cents_int = int(cents)
    if not cents_int:
        return sign + euro_word + " Euro"
    cents_word = "ein" if cents_int == 1 else num2words(cents_int, lang="de")
    if not int(euros.replace(".", "")):
        return sign + cents_word + " Cent"
    return sign + euro_word + " Euro und " + cents_word + " Cent"


def _spoken_date_v3(match: re.Match[str], original: str) -> str:
    day, month, year = map(int, match.group().split("."))
    try:
        date(year, month, day)
    except ValueError:
        return match.group()
    # Only choose the dative ordinal where the immediate preposition is clear.
    prefix = original[:match.start()]
    dative = bool(re.search(r"\b(?:am|vom)\s+$", prefix, re.IGNORECASE))
    ordinal = num2words(day, lang="de", to="ordinal")
    return ordinal + ("n" if dative else "r") + " " + _MONTHS_V3[month] + " " + _spoken_integer_v3(str(year))


def _transform_plain_v3(text: str) -> str:
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    def time_word(match: re.Match[str]) -> str:
        hour, minute = int(match.group("h")), int(match.group("m"))
        if hour > 23 or minute > 59:
            return match.group()
        spoken = num2words(hour, lang="de") + " Uhr"
        return spoken + (" " + num2words(minute, lang="de") if minute else "")
    text = _V3_TIME.sub(time_word, text)
    text = _V3_PARAGRAPH_RANGE.sub(
        lambda m: "Paragrafen " + _spoken_integer_v3(m.group(1))
        + " bis " + _spoken_integer_v3(m.group(2)), text,
    )
    text = _V3_PARAGRAPH.sub(
        lambda m: ("Paragrafen " if m.group(1) == "§§" else "Paragraf ")
        + _spoken_integer_v3(m.group(2)), text,
    )
    text = _V3_YEAR_RANGE.sub(
        lambda m: _spoken_integer_v3(m.group(1)) + " bis " + _spoken_integer_v3(m.group(2)), text
    )
    text = _V3_UNIT.sub(_spoken_unit_v3, text)
    text = _V3_DECIMAL.sub(lambda m: _spoken_decimal_v3(m.group()), text)
    text = _V3_GROUPED.sub(lambda m: _spoken_integer_v3(m.group()), text)
    text = _V3_INTEGER.sub(
        lambda m: _spoken_integer_v3(m.group()) if len(m.group(2)) <= 4 else m.group(), text
    )
    return text


def normalize_german_speech_v3(text: str, glossary: dict[str, str]) -> str:
    """Produce one deterministic German speech prompt without changing book text.

    Explicit glossary matches take precedence over complete technical *terms*
    (including product codes), but never over URLs, emails, dates, currency
    precision, enumeration labels or partial technical identifiers.
    """
    if not isinstance(text, str):
        raise TypeError("Der Vorlesetext muss eine Zeichenfolge sein.")
    validate_german_glossary(glossary)
    if not text:
        return ""
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    pattern = _compiled_glossary_v3(tuple(sorted(glossary)))
    protections = list(_PROTECTED_V3.finditer(text))
    covered_protections: set[int] = set()
    # A single, nonrecursive list of disjoint events. Glossary rules can span
    # hyphenated product identifiers without opening up URL/email substrings.
    events: list[tuple[int, int, str, re.Match[str]]] = []
    if pattern is not None:
        for match in pattern.finditer(text):
            overlapped = [
                p for p in protections
                if p.start() < match.end() and p.end() > match.start()
            ]
            if any(
                p.lastgroup != "technical"
                or match.start() > p.start() or match.end() < p.end()
                for p in overlapped
            ):
                continue
            for item in overlapped:
                covered_protections.add(item.start())
            events.append((match.start(), match.end(), "glossary", match))
    for item in protections:
        if item.start() not in covered_protections:
            events.append((item.start(), item.end(), "protected", item))
    events.sort(key=lambda event: event[0])
    folded = {" ".join(key.split()).casefold(): value for key, value in glossary.items()}
    pieces: list[str] = []
    cursor = 0
    for start, end, kind, match in events:
        if start < cursor:
            continue
        pieces.append(_transform_plain_v3(text[cursor:start]))
        if kind == "glossary":
            value = folded.get(" ".join(match.group().split()).casefold())
            if value is None:
                pieces.append(match.group())
            elif match.group()[0].isupper() and value[0].islower():
                pieces.append(value[0].upper() + value[1:])
            else:
                pieces.append(value)
        elif match.lastgroup == "date":
            pieces.append(_spoken_date_v3(match, text))
        else:
            pieces.append(match.group())
        cursor = end
    pieces.append(_transform_plain_v3(text[cursor:]))
    # One common punctuation/spacing pass; no broad per-digit expansion.
    return PromptNormalizer.normalize_prompt("".join(pieces), language_code="")
