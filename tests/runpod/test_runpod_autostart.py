"""Offline contract tests for the RunPod auto-start entrypoint."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTOSTART = REPO_ROOT / "runpod" / "autostart.sh"


class TestRunPodAutostart(unittest.TestCase):
    def call(self, *arguments: str, extra_env: dict[str, str] | None = None):
        env = {**os.environ, **(extra_env or {})}
        return subprocess.run(
            ["bash", str(AUTOSTART), *arguments],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_shell_syntax(self):
        subprocess.run(
            ["bash", "-n", str(AUTOSTART)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_help_does_not_contact_network_or_install(self):
        result = self.call("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RUNPOD_TTS_AUTOSTART_MODEL", result.stdout)
        self.assertIn("runpod/README.md", result.stdout)

    def test_check_only_prints_persistent_paths_without_installing(self):
        with tempfile.TemporaryDirectory() as workspace:
            env = {
                "RUNPOD_WORKSPACE": workspace,
                "RUNPOD_TTS_MODELS": "both",
                "RUNPOD_TTS_AUTOSTART_MODEL": "qwen",
            }
            result = self.call("--check-only", extra_env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(workspace, result.stdout)
            self.assertIn("feature/german-audiobook-release-hardening", result.stdout)
            self.assertIn("Autostart: qwen", result.stdout)
            self.assertFalse((Path(workspace) / "tts-audiobook-tool").exists())

    def test_help_default_matches_effective_auto_start_default(self):
        help_response = self.call("--help")
        self.assertEqual(help_response.returncode, 0)
        self.assertIn("RUNPOD_TTS_MODELS           qwen | moss | both (default: qwen)", help_response.stdout)
        with tempfile.TemporaryDirectory() as workspace:
            actual = self.call("--check-only", extra_env={
                "RUNPOD_WORKSPACE": workspace,
                "RUNPOD_TTS_MODELS": "qwen",
                "RUNPOD_TTS_AUTOSTART_MODEL": "qwen",
            })
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertIn("Install: qwen", actual.stdout)
        self.assertIn("Autostart: qwen", actual.stdout)

    def test_readme_separates_web_studio_from_legacy_tui(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("15 GB", readme)
        self.assertIn("60 GB Network Volume", readme)
        self.assertIn("Abgrenzung: deutsches Web-Studio und ursprüngliche Konsolenanwendung", readme)
        self.assertIn("Ursprüngliche TUI (Konsole)", readme)
        self.assertIn("keine Benutzer-Stimmklone oder Referenzaufnahme", readme)
        self.assertLess(readme.index("RunPod-Template vollständig einrichten"), readme.index("# Table of contents"))

    def test_invalid_model_is_rejected_without_side_effects(self):
        result = self.call(
            "--check-only",
            extra_env={"RUNPOD_TTS_MODELS": "unknown"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid RUNPOD_TTS_MODELS", result.stderr)

    def test_incompatible_start_model_is_rejected(self):
        result = self.call(
            "--check-only",
            extra_env={
                "RUNPOD_TTS_MODELS": "qwen",
                "RUNPOD_TTS_AUTOSTART_MODEL": "moss",
            },
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be installed", result.stderr)

    def test_random_branches_are_rejected(self):
        result = self.call(
            "--check-only",
            extra_env={"RUNPOD_TTS_BRANCH": "unknown"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unsupported deployment branch", result.stderr)


if __name__ == "__main__":
    unittest.main()
