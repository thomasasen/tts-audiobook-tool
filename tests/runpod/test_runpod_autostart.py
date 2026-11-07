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
            self.assertIn("Branch: master", result.stdout)
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

    def test_generated_web_token_only_reaches_live_pod_stdout_after_health(self):
        source = AUTOSTART.read_text(encoding="utf-8")
        self.assertIn("exec 3>&1", source)
        self.assertLess(source.index("exec 3>&1"), source.index('exec > >(tee -a "$LOG_FILE")'))
        self.assertIn("/api/health", source)
        self.assertIn("if (( WEB_HEALTHY )); then", source)
        self.assertIn('if [[ -n "${AUDIOBOOK_STUDIO_TOKEN:-}" ]]; then', source)
        self.assertIn('WEB_ACCESS_TOKEN="$(<"$TOKEN_FILE")"', source)
        self.assertIn('"Zugangsschlüssel: $WEB_ACCESS_TOKEN"', source)
        self.assertIn("'VERTRAULICH: Nur in privaten RunPod-Container-Logs anzeigen; nicht weitergeben.' >&3", source)
        self.assertIn("exec 3>&-", source)
        self.assertNotIn('echo "$WEB_ACCESS_TOKEN"', source)

    def test_persistent_log_does_not_get_a_live_stdout_secret(self):
        # Exercise the stdout/tee isolation used by the autostart script:
        # fd 3 bypasses tee but remains visible to the container log collector.
        with tempfile.TemporaryDirectory() as work:
            log = Path(work) / "autostart.log"
            script = (
                'exec 3>&1; '
                'exec > >(tee -a "$1") 2>&1; '
                'echo "ordinary boot output"; '
                'printf "generated-token-12345\\n" >&3; '
                'sleep 0.2'
            )
            result = subprocess.run(
                ["bash", "-c", script, "bash", str(log)],
                capture_output=True, text=True, timeout=5, check=True,
            )
            self.assertIn("generated-token-12345", result.stdout)
            self.assertIn("ordinary boot output", log.read_text(encoding="utf-8"))
            self.assertNotIn("generated-token-12345", log.read_text(encoding="utf-8"))

    def test_generated_frontend_does_not_block_git_updates(self):
        source = AUTOSTART.read_text(encoding="utf-8")
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn('status --porcelain --untracked-files=no', source)
        self.assertIn("web/dist/", ignore)
        self.assertIn("web/.vite/", ignore)
        self.assertIn("refusing to deploy stale code", source)

        # Demonstrate the failure case of a fresh build in a real local git
        # checkout, without a network call or a RunPod GPU.
        with tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", root], check=True)
            repository = Path(root)
            (repository / "README.md").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "README.md"], check=True)
            subprocess.run(["git", "-C", root, "-c", "user.name=CI",
                            "-c", "user.email=ci@example.invalid",
                            "commit", "-qm", "init"], check=True)
            output = repository / "web" / "dist" / "index.html"
            output.parent.mkdir(parents=True)
            output.write_text("<html>built</html>\n", encoding="utf-8")
            def status(*args: str) -> str:
                return subprocess.run(
                    ["git", "-C", root, "status", "--porcelain", *args],
                    check=True, text=True, capture_output=True,
                ).stdout.strip()
            self.assertIn("web/", status())
            self.assertEqual(status("--untracked-files=no"), "")
            # But a real tracked modification must still block deployment.
            (repository / "README.md").write_text("modified\n", encoding="utf-8")
            self.assertIn("README.md", status("--untracked-files=no"))

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
