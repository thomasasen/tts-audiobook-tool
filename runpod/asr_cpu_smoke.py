"""Opt-in genuine CPU-ASR smoke test (no GPU, private books or voice uploads).

Requirements: faster-whisper, ctranslate2, espeak-ng, network/model cache for
the first tiny-Whisper download. CI uses a synthetic German eSpeak voice only.
This exercises real inference, NOT a claim of audiobook narration quality.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from tts_audiobook_tool.web_studio import asr_quality


def main() -> None:
    os.environ["AUDIOBOOK_STUDIO_ASR_MODEL"] = "tiny"
    asr_quality._MODEL = None
    asr_quality._LOADED_NAME = None
    asr_quality._FAILED_NAME = None
    assert asr_quality.capabilities()["state"] == "not_loaded", "Whisper is not installed"
    text = (
        "Guten Morgen. Ich lese heute ein deutsches Fachbuch vor. "
        "Die Mitarbeiter besprechen einen guten Plan für die nächste Woche. "
        "Eine verständliche Aussprache ist sehr wichtig."
    )
    with tempfile.TemporaryDirectory(prefix="asr-cpu-smoke-") as tmp:
        wav = Path(tmp) / "synthetic-german.wav"
        subprocess.run(["espeak-ng", "-v", "de", "-s", "125", "-w", str(wav), text],
                       check=True, capture_output=True, timeout=30)
        assert wav.stat().st_size > 10_000, "No synthesized German audio"
        actual = asr_quality.evaluate(wav, text)
        assert actual["status"] in ("passed", "review"), actual["reason"]
        assert actual["asr_state"] == "ready"
        assert actual["asr_recognized_words"] > 0, "ASR produced an empty transcript"
        assert actual["asr_expected_words"] > 0
        assert 0 <= actual["asr_wer"]
        assert 0 <= actual["asr_cer"]
        # Negative case must not be counted as an ASR pass; error text/path
        # and private transcript cannot be serialized to the report.
        missing = asr_quality.evaluate(Path(tmp) / "missing.wav", text)
        assert missing["status"] == "not_checked", missing
        assert missing["asr_state"] == "failed"
        for report in (actual, missing):
            assert "transcript" not in report
            assert tmp not in str(report)
            assert text not in str(report)
    print("Real German CPU-ASR smoke: inference, metrics and negative case exercised; no quality rating.")


if __name__ == "__main__":
    main()
