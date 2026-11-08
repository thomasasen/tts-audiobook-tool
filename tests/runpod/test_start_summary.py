"""Contract and security tests for the end-of-start RunPod summary banner."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "runpod/print-start-summary.sh"
AUTOSTART = ROOT / "runpod/autostart.sh"
SECRET = "runpod-test-secret-not-for-production-only-0123456789"


class TestStartSummary(unittest.TestCase):
    def render(self, data_root: Path, **config):
        env = {
            **os.environ,
            "RUNPOD_SUMMARY_DATA_ROOT": str(data_root),
            "RUNPOD_SUMMARY_WORKSPACE": str(data_root.parent),
            "RUNPOD_SUMMARY_WEB": "ready",
            "RUNPOD_SUMMARY_THORSTEN": "installed",
            "RUNPOD_SUMMARY_CUDA_SYSTEM": "passed",
            "RUNPOD_SUMMARY_CUDA_THORSTEN": "passed",
            "RUNPOD_SUMMARY_GPU": "NVIDIA RTX 5000 Ada Generation",
            "RUNPOD_SUMMARY_COMMIT": "f00ba42",
            "RUNPOD_SUMMARY_PORT": "7860",
            "RUNPOD_SUMMARY_TUI": "nicht gestartet (Standard)",
        }
        env.pop("AUDIOBOOK_STUDIO_TOKEN", None)
        env.update(config)
        return subprocess.run(
            ["bash", str(SUMMARY)],
            env=env, capture_output=True, text=True, check=False, timeout=10,
        )

    def test_success_banner_at_end_with_login_and_real_status(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "audiobook-data"
            data.mkdir()
            (data / "studio-access-token").write_text(SECRET)
            got = self.render(data)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("GERMAN AUDIOBOOK STUDIO", got.stdout)
        self.assertIn("START ABGESCHLOSSEN: WEB + THORSTEN INSTALLIERT", got.stdout)
        self.assertIn("RunPod > Connect > HTTP 7860", got.stdout)
        self.assertIn("NVIDIA RTX 5000 Ada Generation", got.stdout)
        self.assertIn("f00ba42 (master)", got.stdout)
        self.assertIn("CUDA (Thorsten)", got.stdout)
        self.assertIn("passed", got.stdout)
        self.assertIn("ZUGANGSSCHLUESSEL", got.stdout)
        self.assertIn(SECRET, got.stdout)
        self.assertIn("Echte Hoerprobe erzeugen", got.stdout)

    def test_warning_banner_never_claims_ready_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "audiobook-data"
            data.mkdir()
            got = self.render(
                data,
                RUNPOD_SUMMARY_THORSTEN="not-ready",
                RUNPOD_SUMMARY_CUDA_THORSTEN="not-tested",
            )
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("WEB BEREIT: SPRECHER NOCH NICHT BETRIEBSBEREIT", got.stdout)
        self.assertIn("AKTION ERFORDERLICH", got.stdout)
        self.assertNotIn("Echte Hoerprobe erzeugen", got.stdout)
        self.assertIn("Noch nicht betriebsbereit", got.stdout)
        self.assertNotIn("WEB + THORSTEN INSTALLIERT", got.stdout)
        self.assertNotIn("ZUGANGSSCHLUESSEL", got.stdout)

    def test_failed_web_shows_failure_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "audiobook-data"
            data.mkdir()
            got = self.render(data, RUNPOD_SUMMARY_WEB="failed")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("START FEHLGESCHLAGEN: WEB-SERVER NICHT ERREICHBAR", got.stdout)
        self.assertIn("NICHT ERREICHBAR", got.stdout)
        self.assertIn("tmux capture-pane", got.stdout)
        self.assertNotIn("START ABGESCHLOSSEN", got.stdout)

    def test_custom_token_is_never_exposed_even_when_generated_file_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "audiobook-data"
            data.mkdir()
            (data / "studio-access-token").write_text(SECRET)
            custom = "private-environment-token-1234567890987654321"
            got = self.render(data, AUDIOBOOK_STUDIO_TOKEN=custom)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertNotIn(custom, got.stdout)
        self.assertNotIn(SECRET, got.stdout)
        self.assertIn("Eigener AUDIOBOOK_STUDIO_TOKEN", got.stdout)

    def test_symlink_access_token_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "audiobook-data"
            data.mkdir()
            other = Path(folder) / "sensitive"
            other.write_text(SECRET)
            (data / "studio-access-token").symlink_to(other)
            got = self.render(data)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertNotIn(SECRET, got.stdout)
        self.assertIn("Token-Datei fehlt", got.stdout)

    def test_autostart_keeps_secret_out_of_tee_and_summary_is_last(self):
        content = AUTOSTART.read_text(encoding="utf-8")
        summary = 'bash "$REPO_DIR/runpod/print-start-summary.sh" >&3'
        self.assertIn('exec 3>&1', content)
        self.assertIn('exec > >(tee -a "$LOG_FILE") 2>&1', content)
        self.assertIn(summary, content)
        self.assertLess(content.index('exec 3>&1'), content.index('exec > >(tee -a "$LOG_FILE")'))
        self.assertLess(
            content.index('if [[ "$RUNPOD_TTS_MODELS" != none ]]; then'),
            content.index(summary),
        )
        self.assertLess(content.index('FINAL_WEB_STATUS=failed'), content.index(summary))
        self.assertIn('exec 3>&-\n\nif [[ "$FINAL_WEB_STATUS" != ready ]]; then', content)
        self.assertEqual(content.count(summary), 1)
        # Token path appears only in the secure helper and the already-guarded
        # early private print, not in any ordinary persistent summary line.
        self.assertIn('echo "RunPod start steps complete:', content)
        self.assertNotIn('echo "Zugangsschluessel:', content)

    def test_actual_fd3_bypasses_persistent_tee(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "audiobook-data"
            data.mkdir()
            (data / "studio-access-token").write_text(SECRET)
            log = root / "autostart.log"
            script = (
                'exec 3>&1; '
                'exec > >(tee -a "$1") 2>&1; '
                'echo "preparation complete"; '
                'RUNPOD_SUMMARY_DATA_ROOT="$2" '
                'RUNPOD_SUMMARY_WORKSPACE="$3" '
                'RUNPOD_SUMMARY_WEB=ready '
                'bash "$4" >&3; sleep 0.2'
            )
            env = {**os.environ}
            env.pop("AUDIOBOOK_STUDIO_TOKEN", None)
            result = subprocess.run(
                ["bash", "-c", script, "bash", str(log), str(data), str(root), str(SUMMARY)],
                env=env, capture_output=True, text=True, timeout=10, check=True,
            )
            saved = log.read_text()
            self.assertIn(SECRET, result.stdout)
            self.assertIn("preparation complete", saved)
            self.assertNotIn(SECRET, saved)
            self.assertNotIn("ZUGANGSSCHLUESSEL", saved)

    def test_banner_script_syntax(self):
        subprocess.run(["bash", "-n", str(SUMMARY)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
