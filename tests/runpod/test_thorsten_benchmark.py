"""CPU-only checks for the RunPod Thorsten benchmark harness."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runpod" / "benchmark_thorsten.py"
SPEC = importlib.util.spec_from_file_location("thorsten_benchmark_under_test", SCRIPT)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class TestThorstenBenchmark(unittest.TestCase):
    def test_validates_pcm_and_does_not_include_text_in_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(struct.pack("<h", 1200) * 24000)
            duration, digest = benchmark._validate_wave(path)
            self.assertEqual(duration, 1.0)
            self.assertEqual(len(digest), 64)
            with path.open("r+b") as f:
                f.truncate(128)
            with self.assertRaises((ValueError, EOFError, wave.Error)):
                benchmark._validate_wave(path)

    def test_summary_real_time_factors_and_profile_distribution(self):
        rows = [
            {"profile": "short", "audio_seconds": 5, "inference_seconds": 2,
             "wav_seconds": 0.1, "validation_seconds": 0.2,
             "peak_allocated_vram_bytes": 1234},
            {"profile": "medium", "audio_seconds": 10, "inference_seconds": 3,
             "wav_seconds": 0.2, "validation_seconds": 0.3,
             "peak_allocated_vram_bytes": 5678},
        ]
        s = benchmark._summary(rows, elapsed=8.0)
        self.assertEqual(s["segments"], 2)
        self.assertEqual(s["audio_seconds"], 15)
        self.assertEqual(s["inference_rtf"], round(5 / 15, 6))
        self.assertEqual(s["pipeline_rtf"], round(8 / 15, 6))
        self.assertEqual(s["audio_minutes_per_wall_minute"], round(15 / 8, 4))
        self.assertEqual(s["peak_allocated_vram_bytes"], 5678)
        self.assertEqual(s["profiles"]["short"]["count"], 1)
        self.assertEqual(s["profiles"]["medium"]["count"], 1)

    def test_refuses_to_download_or_run_if_model_install_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(
                python=str(Path(tmp) / "missing-python"),
                model_root=tmp,
                profile="all", speed=1.0, warmup=0, repeats=1,
                mode="compare", sampling_interval=0.3,
            )
            with self.assertRaises(FileNotFoundError):
                benchmark.benchmark(args)

    def test_sampler_missing_nvidia_smi_is_not_a_gpu_measurement(self):
        sampler = benchmark.GpuSampler(0.1)
        self.assertEqual(sampler.summary()["gpu_samples"], 0)
        self.assertIsNone(sampler.summary()["gpu_utilization_mean_pct"])


if __name__ == "__main__":
    unittest.main()
