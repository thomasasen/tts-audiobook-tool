"""Offline integration tests for actual CUDA allocation preflight.

Use a fake torch module in isolated interpreters to simulate a visible GPU
that rejects cudaSetDevice, without requiring GPUs or downloading weights.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "runpod/gpu_preflight.py"
SPEC = importlib.util.spec_from_file_location("gpu_preflight", PATH)
assert SPEC is not None and SPEC.loader is not None
gpu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gpu)


class TestGpuPreflight(unittest.TestCase):
    def create_fake_runtime(self, work: Path, name: str) -> None:
        venv = work / "venvs" / name / "bin"
        venv.mkdir(parents=True, exist_ok=True)
        (venv / "python").symlink_to(sys.executable)

    def create_fake_torch(self, work: Path) -> None:
        fake = work / "torch.py"
        fake.write_text("""import os
from types import SimpleNamespace
__version__ = '2.8.0+cu128'
version = SimpleNamespace(cuda='12.8')
def init():
    if os.environ.get('FAKE_GPU_ERROR') == '1':
        raise RuntimeError('CUDA error 46: device busy or unavailable')
def is_available():
    return True
def get_device_name(index):
    return 'Fake NVIDIA GPU'
def synchronize():
    return None
def get_device_capability(index):
    return (int(os.environ.get('FAKE_GPU_MAJOR', '8')),
            int(os.environ.get('FAKE_GPU_MINOR', '9')))
cuda = SimpleNamespace(init=init, is_available=is_available,
    synchronize=synchronize, get_device_name=get_device_name,
    get_device_capability=get_device_capability)
def ones(count, device):
    assert device == 'cuda:0'
    if os.environ.get('FAKE_GPU_ERROR') == '1':
        raise RuntimeError('CUDA error 46: device busy or unavailable')
    return [1] * count
""", encoding="utf-8")

    def test_qwen_cuda_allocation_succeeds_when_operational(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_runtime(work, "qwen")
            self.create_fake_torch(work)
            with patch.dict(os.environ, {"PYTHONPATH": str(work), "FAKE_GPU_ERROR": "0"}):
                report = gpu.check_runtime("qwen", work)
            self.assertTrue(report["cuda_visible"])
            self.assertTrue(report["cuda_operational"])
            self.assertEqual(report["device"], "Fake NVIDIA GPU")
            self.assertEqual(report["torch_cuda"], "12.8")
            self.assertEqual(report["compute_capability"], [8, 9])

    def test_cuda_visible_but_actual_allocation_fails_46(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_runtime(work, "thorsten")
            self.create_fake_torch(work)
            with patch.dict(os.environ, {"PYTHONPATH": str(work), "FAKE_GPU_ERROR": "1"}):
                report = gpu.check_runtime("thorsten", work)
            self.assertTrue(report["cuda_visible"])
            self.assertFalse(report["cuda_operational"])
            self.assertIn("CUDA error 46", report["error"])

    def test_smi_success_never_overrides_failed_torch_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_runtime(work, "thorsten")
            self.create_fake_torch(work)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            smi = bin_dir / "nvidia-smi"
            smi.write_text("#!/bin/sh\necho 'RTX 5000 Ada, 590.48.01, 5'\n", encoding="utf-8")
            smi.chmod(0o755)
            with patch.dict(os.environ, {
                "PYTHONPATH": str(work),
                "FAKE_GPU_ERROR": "1",
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            }):
                report = gpu.inspect(work, ("thorsten",))
            self.assertTrue(report["gpu_visible_via_nvidia_smi"])
            self.assertEqual(report["gpu_name"], "RTX 5000 Ada")
            self.assertFalse(report["ready"])

    def test_missing_runtime_does_not_fake_success(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            result = gpu.check_runtime("qwen", work)
        self.assertFalse(result["cuda_operational"])
        self.assertIn("not installed", result["reason"])

    def test_probe_timeout_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_runtime(work, "qwen")
            with patch.object(gpu.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=50)):
                result = gpu.check_runtime("qwen", work)
            self.assertFalse(result["cuda_operational"])
            self.assertIn("timed out", result["reason"])

    def test_malformed_stdout_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_runtime(work, "qwen")
            with patch.object(gpu.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="not-json", stderr="")):
                result = gpu.check_runtime("qwen", work)
            self.assertFalse(result["cuda_operational"])

    def test_system_python_cuda_preflight_does_not_require_tts_venvs(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_torch(work)
            with patch.dict(os.environ, {
                "PYTHONPATH": str(work),
                "FAKE_GPU_ERROR": "0",
                "AUDIOBOOK_DATA_ROOT": str(work),
            }):
                result = subprocess.run(
                    [sys.executable, str(PATH), "--env", "system", "--json"],
                    capture_output=True, text=True, timeout=20,
                )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["runtimes"][0]["name"], "system")
            self.assertTrue(payload["runtimes"][0]["cuda_operational"])
            self.assertTrue(payload["ready"])

    def test_blackwell_5090_rejected_even_when_system_cuda_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_torch(work)
            with patch.dict(os.environ, {
                "PYTHONPATH": str(work),
                "FAKE_GPU_ERROR": "0",
                "FAKE_GPU_MAJOR": "12",
                "FAKE_GPU_MINOR": "0",
                "AUDIOBOOK_DATA_ROOT": str(work),
            }):
                result = subprocess.run(
                    [sys.executable, str(PATH), "--env", "system", "--json",
                     "--require-thorsten-compatible"],
                    capture_output=True, text=True, timeout=20,
                )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["runtimes"][0]["cuda_operational"])
            self.assertFalse(payload["ready"])
            self.assertFalse(payload["thorsten_compatible"])
            self.assertEqual(payload["thorsten_gpu_compute_capability"], "sm_120")
            self.assertIn("torch 2.3.1+cu121", payload["thorsten_compatibility_error"])

    def test_ada_4090_supported_by_pinned_thorsten_architecture(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_torch(work)
            with patch.dict(os.environ, {
                "PYTHONPATH": str(work),
                "FAKE_GPU_ERROR": "0",
                "FAKE_GPU_MAJOR": "8",
                "FAKE_GPU_MINOR": "9",
                "AUDIOBOOK_DATA_ROOT": str(work),
            }):
                result = subprocess.run(
                    [sys.executable, str(PATH), "--env", "system", "--json",
                     "--require-thorsten-compatible"],
                    capture_output=True, text=True, timeout=20,
                )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ready"])
            self.assertTrue(payload["thorsten_compatible"])

    def test_newer_cuda_on_system_does_not_override_old_thorsten_gpu_support(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_torch(work)
            with patch.dict(os.environ, {
                "PYTHONPATH": str(work),
                "FAKE_GPU_ERROR": "0",
                "FAKE_GPU_MAJOR": "10",
                "FAKE_GPU_MINOR": "0",
                "AUDIOBOOK_DATA_ROOT": str(work),
            }):
                result = subprocess.run(
                    [sys.executable, str(PATH), "--env", "system", "--json",
                     "--require-thorsten-compatible"],
                    capture_output=True, text=True, timeout=20,
                )
            self.assertEqual(result.returncode, 1)
            self.assertIn("sm_100", result.stdout)

    def test_unknown_capability_rejected_in_thorsten_mode(self):
        valid = {"ready": True, "runtimes": [{"name": "system", "cuda_operational": True}]}
        report = gpu.check_thorsten_gpu_compatibility(valid)
        self.assertFalse(report["ready"])
        self.assertFalse(report["thorsten_compatible"])
        self.assertIn("compute capability", report["thorsten_compatibility_error"])

    def test_architecture_gate_requires_current_audited_lock_version(self):
        lock = (ROOT / "runpod/locks/thorsten/pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"torch==2.3.1"', lock)
        self.assertIn("sm_", PATH.read_text(encoding="utf-8"))

    def test_cli_returns_nonzero_for_broken_gpu(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.create_fake_runtime(work, "qwen")
            self.create_fake_torch(work)
            with patch.dict(os.environ, {
                "AUDIOBOOK_DATA_ROOT": str(work),
                "PYTHONPATH": str(work),
                "FAKE_GPU_ERROR": "1",
            }):
                result = subprocess.run(
                    [sys.executable, str(PATH), "--env", "qwen", "--json"],
                    capture_output=True, text=True, timeout=20, check=False,
                )
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertFalse(report["ready"])
            self.assertTrue(report["runtimes"][0]["cuda_visible"])


if __name__ == "__main__":
    unittest.main()
