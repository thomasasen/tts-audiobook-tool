"""Conservative, privacy-safe CPU Whisper checks for the German Web Studio.

ASR is a *review signal*, not proof that the narration is correct.  Neither
reference text nor the recognized transcript is returned or logged here.
"""
from __future__ import annotations

import difflib
import importlib.util
import os
import re
import threading
import unicodedata
from pathlib import Path

SUPPORTED_MODELS = frozenset({"tiny", "base", "small", "medium"})
_LOCK = threading.Lock()
_MODEL = None
_LOADED_NAME: str | None = None
_FAILED_NAME: str | None = None

_MESSAGES = {
    "disabled": "Automatische Spracherkennung ist deaktiviert.",
    "invalid": "ASR-Modellkonfiguration ist ungültig.",
    "missing": "ASR ist in dieser Web-Runtime nicht installiert.",
    "not_loaded": "ASR ist installiert; das Modell wird bei der ersten Prüfung geladen.",
    "ready": "ASR-Modell ist geladen; automatische Prüfungen sind möglich.",
    "failed": "Die ASR-Prüfung ist fehlgeschlagen. Manuelle Hörkontrolle erforderlich.",
}


def _configured_name() -> str:
    return os.environ.get("AUDIOBOOK_STUDIO_ASR_MODEL", "").strip().lower()


def capabilities() -> dict:
    """Report *actual* state, not merely the configured model name."""
    name = _configured_name()
    if name in ("", "off", "none", "disabled"):
        state = "disabled"
    elif name not in SUPPORTED_MODELS:
        state = "invalid"
    else:
        try:
            installed = importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):
            installed = False
        if not installed:
            state = "missing"
        elif _FAILED_NAME == name:
            state = "failed"
        elif _MODEL is not None and _LOADED_NAME == name:
            state = "ready"
        else:
            state = "not_loaded"
    return {"state": state, "message": _MESSAGES[state], "enabled": state not in ("disabled", "invalid", "missing"),
            "model": name if name in SUPPORTED_MODELS else None}


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold(), flags=re.UNICODE)


def _distance(left: list[str] | str, right: list[str] | str) -> int:
    """Levenshtein distance with bounded memory (not SequenceMatcher ratio)."""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, value in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (value != other)))
        previous = current
    return previous[-1]


def compare_text(expected: str, recognized: str) -> dict:
    """Normalized WER/CER plus non-sensitive alignment counts.

    A low score never automatically certifies an audio segment.
    """
    reference = _tokens(expected)
    hypothesis = _tokens(recognized)
    word_edits = _distance(reference, hypothesis)
    expected_chars = " ".join(reference)
    actual_chars = " ".join(hypothesis)
    char_edits = _distance(expected_chars, actual_chars)
    opcodes = difflib.SequenceMatcher(a=reference, b=hypothesis, autojunk=False).get_opcodes()
    changed_blocks = sum(tag != "equal" for tag, *_ in opcodes)
    # A digit discrepancy is a useful explicit review flag, but ASR can render
    # spoken numbers as words. The WER/CER signal still covers that case.
    reference_digits = re.findall(r"\d+", expected)
    hypothesis_digits = re.findall(r"\d+", recognized)
    number_mismatch = bool(reference_digits and hypothesis_digits and reference_digits != hypothesis_digits)
    return {
        "asr_wer": round(word_edits / max(1, len(reference)), 3),
        "asr_cer": round(char_edits / max(1, len(expected_chars)), 3),
        "asr_similarity": round(difflib.SequenceMatcher(
            a=reference, b=hypothesis, autojunk=False).ratio(), 3),
        "asr_expected_words": len(reference),
        "asr_recognized_words": len(hypothesis),
        "asr_changed_blocks": changed_blocks,
        "asr_number_mismatch": number_mismatch,
    }


def evaluate(path: Path, expected: str) -> dict:
    """Check one private WAV; never publish transcript, paths or library errors."""
    state = capabilities()
    result = {"status": "not_checked", "asr_state": state["state"],
              "reason": state["message"], "warnings": [], "asr_similarity": None}
    if state["state"] in ("disabled", "invalid", "missing"):
        return result

    global _MODEL, _LOADED_NAME, _FAILED_NAME
    name = _configured_name()
    try:
        with _LOCK:
            if _MODEL is None or _LOADED_NAME != name:
                from faster_whisper import WhisperModel
                _MODEL = WhisperModel(name, device="cpu", compute_type="int8")
                _LOADED_NAME = name
            segments, _ = _MODEL.transcribe(str(path), language="de", beam_size=3,
                                             condition_on_previous_text=False)
            recognized = " ".join(part.text.strip() for part in segments).strip()
            _FAILED_NAME = None
    except Exception:
        # Exception messages can contain private filesystem paths; do not
        # expose them to the API, stored job metadata or public logging.
        _FAILED_NAME = name
        _MODEL = None
        _LOADED_NAME = None
        result.update(asr_state="failed", reason=_MESSAGES["failed"])
        return result

    metrics = compare_text(expected, recognized)
    result.update(metrics)
    result["asr_state"] = "ready"
    result["reason"] = ""
    suspicious = (metrics["asr_recognized_words"] == 0
                  or metrics["asr_wer"] > 0.30
                  or metrics["asr_cer"] > 0.25
                  or metrics["asr_number_mismatch"])
    result["status"] = "review" if suspicious else "passed"
    if metrics["asr_recognized_words"] == 0:
        result["warnings"].append("ASR konnte keine Wörter erkennen; Segment anhören.")
    elif suspicious:
        result["warnings"].append("ASR-Abweichungen gefunden; Segment anhören und Texttreue prüfen.")
    return result
