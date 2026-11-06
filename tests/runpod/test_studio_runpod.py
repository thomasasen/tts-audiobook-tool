"""Static checks for integrated Web + Thorsten RunPod bootstrap.

No GPU, model downloads, or network required.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class TestGermanStudioRunPod(unittest.TestCase):
    def test_new_install_and_start_scripts_are_valid_bash(self):
        for relative in ("runpod/install-thorsten.sh", "runpod/start-web.sh"):
            with self.subTest(relative=relative):
                subprocess.run(["bash", "-n", str(ROOT / relative)], check=True)

    def test_install_is_persistent_and_not_checking_in_weights(self):
        source = (ROOT / "runpod/install-thorsten.sh").read_text(encoding="utf-8")
        self.assertIn("CosyVoice.git", source)
        self.assertIn("ace7c47", source)
        self.assertIn("Thorsten-Voice/CosyVoice3", source)
        self.assertIn('"$AUDIOBOOK_DATA_ROOT/models', source)
        self.assertIn("spk2info.pt", source)
        self.assertIn("3.11", source)
        self.assertIn('sha256sum --check --status "$CHECKSUMS"', source)
        self.assertIn("Refusing to trust or silently rebaseline weights", source)

    def test_readme_command_points_to_exact_integrated_autostart(self):
        root = (ROOT / "README.md").read_text(encoding="utf-8")
        autostart = (ROOT / "runpod/autostart.sh").read_text(encoding="utf-8")
        self.assertIn(
            "feature/german-audiobook-release-validation/runpod/autostart.sh", root
        )
        self.assertIn(
            'RUNPOD_TTS_BRANCH:=feature/german-audiobook-release-validation', autostart
        )
        self.assertIn("runpod/start-web.sh", autostart)
        self.assertIn("runpod/install-thorsten.sh", autostart)

    def test_web_is_access_key_protected_single_process(self):
        code = (ROOT / "runpod/start-web.sh").read_text(encoding="utf-8")
        self.assertIn("studio-access-token", code)
        self.assertIn("chmod 600", code)
        self.assertIn("AUDIOBOOK_STUDIO_TOKEN", code)
        self.assertIn("--workers 1", code)
        self.assertIn("AUDIOBOOK_THORSTEN_PYTHON", code)
        self.assertIn("AUDIOBOOK_THORSTEN_SCRIPT", code)


if __name__ == "__main__":
    unittest.main()
