"""CPU-only worker-pool protocol tests: synthetic models and private WAVs only."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import wave

import pytest

from tts_audiobook_tool.web_studio.parallel_narration import ParallelWorkerPool
from tts_audiobook_tool.web_studio import production


FAKE_WORKER = '''
import json, sys, time, wave
from pathlib import Path
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    marker = Path.cwd() / "concurrency-markers"
    with marker.open("a", encoding="utf-8") as handle:
        handle.write(req["id"] + "\\n")
    # All first two requests must be active simultaneously, not serialized.
    until = time.monotonic() + 3
    while len(marker.read_text(encoding="utf-8").splitlines()) < 2:
        if time.monotonic() > until:
            print(json.dumps({"id": req["id"], "ok": False}), flush=True)
            break
        time.sleep(0.01)
    else:
        with wave.open(req["output"], "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes((1200).to_bytes(2, "little", signed=True) * 16000)
        print(json.dumps({"id": req["id"], "ok": True}), flush=True)
'''


def fake_runtime(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    official = model / "infer_thorsten.py"
    official.write_text("# fake, never executed", encoding="utf-8")
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    return (sys.executable, str(official)), worker


def test_two_model_processes_execute_independently(tmp_path):
    config, script = fake_runtime(tmp_path)
    a, b = tmp_path / "a.partial.wav", tmp_path / "b.partial.wav"
    pool = ParallelWorkerPool(config, 2, tmp_path, worker_script=script)
    try:
        first = pool.submit("Erstes Testsegment.", a, 1.0)
        second = pool.submit("Zweites Testsegment.", b, 1.0)
        assert first.result(timeout=8) > 0
        assert second.result(timeout=8) > 0
        for candidate in (a, b):
            with wave.open(str(candidate), "rb") as wav:
                assert wav.getnchannels() == 1
                assert wav.getsampwidth() == 2
                assert wav.getnframes() == 16000
        marker = tmp_path / "model" / "concurrency-markers"
        assert len(marker.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        pool.close()
        pool.close()
    assert not any(worker.proc is not None for worker in pool._workers)


def test_parallel_pool_rejects_unbounded_or_nonpartial_work(tmp_path):
    config, script = fake_runtime(tmp_path)
    with pytest.raises(ValueError, match="2-4"):
        ParallelWorkerPool(config, 5, tmp_path, worker_script=script)
    pool = ParallelWorkerPool(config, 2, tmp_path, worker_script=script)
    try:
        with pytest.raises(ValueError, match="partial"):
            pool.submit("Text", tmp_path / "committed.wav", 1.0)
        with pytest.raises(ValueError, match="length"):
            pool.submit("", tmp_path / "missing.partial.wav", 1.0)
    finally:
        pool.close()
    with pytest.raises(RuntimeError, match="closed"):
        pool.submit("Text", tmp_path / "closed.partial.wav", 1.0)


@pytest.mark.parametrize("configured,expected", [
    (None, 2), ("1", 1), ("2", 2), ("4", 4),
])
def test_new_job_worker_count_defaults_to_two_but_allows_explicit_override(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("AUDIOBOOK_STUDIO_TTS_WORKERS", raising=False)
    else:
        monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", configured)
    assert production._parallel_worker_count() == expected


def test_invalid_or_legacy_nonpersistent_parallel_flags_rejected(monkeypatch):
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", "10")
    with pytest.raises(ValueError, match="1, 2, 3 oder 4"):
        production._parallel_worker_count()
    monkeypatch.setenv("AUDIOBOOK_STUDIO_TTS_WORKERS", "2")
    monkeypatch.setenv("AUDIOBOOK_STUDIO_PERSISTENT_TTS", "0")
    with pytest.raises(ValueError, match="persistenten"):
        production._parallel_worker_count()
