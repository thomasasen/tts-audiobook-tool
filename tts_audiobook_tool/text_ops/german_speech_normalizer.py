"""Conservative German speech-prompt transformations, never EPUB mutations.

This module is deliberately model-agnostic. Ambiguous identifiers, times,
dates, URLs and e-mail addresses are left to an explicitly audited glossary
or the speaker model rather than guessed.
"""
from __future__ import annotations

import re

from num2words import num2words

from tts_audiobook_tool.text_ops.prompt_normalizer import PromptNormalizer

VERSION = "de-narration-v2"

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
