"""Offline ASR state, word-error, privacy and failure regression tests.

Mocks exercise control flow here; runpod/asr_cpu_smoke.py is the separate
genuine faster-whisper CPU-inference test, not a mocked acceptance test.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tts_audiobook_tool.web_studio import asr_quality


@pytest.fixture(autouse=True)
def reset_asr(monkeypatch):
    monkeypatch.setattr(asr_quality, "_MODEL", None)
    monkeypatch.setattr(asr_quality, "_LOADED_NAME", None)
    monkeypatch.setattr(asr_quality, "_FAILED_NAME", None)
    monkeypatch.delenv("AUDIOBOOK_STUDIO_ASR_MODEL", raising=False)


def test_disabled_invalid_and_missing_are_never_advertised_as_ready(monkeypatch):
    assert asr_quality.capabilities()["state"] == "disabled"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "off")
    assert asr_quality.capabilities()["state"] == "disabled"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "unknown-model")
    assert asr_quality.capabilities()["state"] == "invalid"
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "small")
    monkeypatch.setattr(asr_quality.importlib.util, "find_spec", lambda name: None)
    assert asr_quality.capabilities()["state"] == "missing"
    result = asr_quality.evaluate(Path("/does-not-exist.wav"), "Privater Text.")
    assert result["status"] == "not_checked"
    assert result["asr_state"] == "missing"
    assert "Privater" not in str(result)


def test_real_edit_distance_not_only_sequence_similarity():
    identical = asr_quality.compare_text("Guten Tag, Berlin.", "guten tag berlin")
    assert identical["asr_wer"] == 0
    assert identical["asr_cer"] == 0
    numbers = asr_quality.compare_text(
        "Der Preis beträgt 49 Euro.", "Der Preis beträgt 40 Euro.")
    assert numbers["asr_wer"] == 0.2
    assert numbers["asr_number_mismatch"] is True
    omission = asr_quality.compare_text("Eins zwei drei vier.", "Eins drei.")
    assert omission["asr_wer"] == 0.5
    assert omission["asr_changed_blocks"] >= 1
    assert asr_quality.compare_text("Hallo", "")["asr_wer"] == 1.0


def test_transcription_uses_cpu_and_keeps_private_words_off_results(monkeypatch, tmp_path):
    name = "faster_whisper"
    module = ModuleType(name)
    calls = []

    class FakeWhisper:
        def __init__(self, model, *, device, compute_type):
            calls.append((model, device, compute_type))

        def transcribe(self, path, **options):
            assert options["language"] == "de"
            assert options["condition_on_previous_text"] is False
            return iter([SimpleNamespace(text="Der Preis beträgt 40 Euro.")]), {}

    module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, name, module)
    finder = asr_quality.importlib.util.find_spec
    monkeypatch.setattr(asr_quality.importlib.util, "find_spec",
                        lambda package: object() if package == name else finder(package))
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "tiny")
    assert asr_quality.capabilities()["state"] == "not_loaded"
    result = asr_quality.evaluate(tmp_path / "private.wav", "Der Preis beträgt 49 Euro.")
    assert calls == [("tiny", "cpu", "int8")]
    assert result["status"] == "review"
    assert result["asr_number_mismatch"] is True
    assert result["asr_state"] == "ready"
    assert "transcript" not in result
    assert "49 Euro" not in str(result)
    assert "40 Euro" not in str(result)
    assert asr_quality.capabilities()["state"] == "ready"


def test_asr_exception_is_not_a_pass_and_cannot_leak_private_paths(monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_ASR_MODEL", "tiny")
    monkeypatch.setattr(asr_quality.importlib.util, "find_spec", lambda name: object())

    class BrokenWhisper:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("private original book text at /secret/voice.wav")

    module = ModuleType("faster_whisper")
    module.WhisperModel = BrokenWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    result = asr_quality.evaluate(Path("/secret/voice.wav"), "Vertraulicher Buchtext.")
    assert result["status"] == "not_checked"
    assert result["asr_state"] == "failed"
    assert asr_quality.capabilities()["state"] == "failed"
    assert "/secret/" not in str(result)
    assert "Vertraulicher" not in str(result)
