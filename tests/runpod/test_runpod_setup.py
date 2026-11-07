"""Dependency-free tests for opt-in RunPod setup support.

Not a replacement for real GPU model inference and audio listening tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = ROOT / "runpod" / "doctor.py"
spec = importlib.util.spec_from_file_location("runpod_doctor", DOCTOR_PATH)
assert spec is not None and spec.loader is not None
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class TestRunPodSupport(unittest.TestCase):
    def test_shell_syntax_and_help_never_install(self):
        for filename in ("env.sh", "model-source.sh", "setup.sh", "start.sh", "generate-release-locks.sh"):
            with self.subTest(filename=filename):
                subprocess.run(
                    ["bash", "-n", str(ROOT / "runpod" / filename)],
                    check=True, capture_output=True, text=True,
                )
        for filename in ("setup.sh", "start.sh"):
            result = subprocess.run(
                ["bash", str(ROOT / "runpod" / filename), "--help"],
                check=True, capture_output=True, text=True,
            )
            self.assertIn("Usage:", result.stdout)

    def test_environment_uses_volume_and_keeps_paths_out_of_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; printf "%s\\n%s\\n%s\\n%s\\n" "$AUDIOBOOK_VENV_DIR" "$HF_HOME" "$AUDIOBOOK_REFERENCES_DIR" "$AUDIOBOOK_APP_HOME"',
                 "bash", str(ROOT / "runpod" / "env.sh")],
                env={**os.environ, "RUNPOD_WORKSPACE": tmp, "AUDIOBOOK_DATA_ROOT": ""},
                check=True, capture_output=True, text=True,
            )
            lines = result.stdout.strip().splitlines()
            self.assertEqual(len(lines), 4)
            self.assertTrue(all(x.startswith(tmp) for x in lines))
            self.assertNotIn(str(ROOT), result.stdout)

    def test_diagnostic_non_strict_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["python3", str(DOCTOR_PATH), "--json"],
                env={**os.environ, "RUNPOD_WORKSPACE": tmp, "AUDIOBOOK_DATA_ROOT": str(Path(tmp) / "audiobook-data")},
                check=True, capture_output=True, text=True,
            )
            data = json.loads(result.stdout)
            self.assertIn("nvidia_smi", data["checks"])
            self.assertIn("qwen", data["checks"]["model_envs"])
            self.assertIn("moss", data["checks"]["model_envs"])
            self.assertEqual(data["errors"], [])

    def test_missing_gpu_and_mount_fail_strict(self):
        report = {
            "workspace_is_mount": False,
            "workspace_writable": True,
            "nvidia_smi": {"ok": False},
            "ffmpeg": True,
            "ffprobe": True,
            "git": True,
            "portaudio": True,
            "model_envs": {"qwen": {"installed": False}, "moss": {"installed": False}},
        }
        errors = doctor.check_strict(report, "both")
        self.assertTrue(any("persistent" in x for x in errors))
        self.assertTrue(any("nvidia-smi" in x for x in errors))
        self.assertTrue(any("qwen" in x for x in errors))
        self.assertTrue(any("moss" in x for x in errors))

    def test_model_probe_version_mismatch_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "venvs/qwen/bin/python"
            exe.parent.mkdir(parents=True)
            exe.write_text("fake python")
            with mock.patch.object(doctor, "run_command", return_value={
                "ok": True,
                "output": json.dumps({"torch": "2.9.1+cu128", "cuda": True, "module": True}),
                "error": "",
            }):
                result = doctor.inspect_model("qwen", Path(tmp))
            self.assertTrue(result["installed"])
            self.assertFalse(result["version_ok"])


if __name__ == "__main__":
    unittest.main()
